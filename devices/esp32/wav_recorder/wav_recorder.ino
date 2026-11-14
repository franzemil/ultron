/*
 * wav_recorder — Seeed XIAO ESP32S3 Sense
 *
 * Ultron T4: records a 16 kHz / 16-bit / mono WAV clip from the built-in
 * PDM microphone to the SD card, labeled by phrase, for training data
 * (US-002 / T4). Serial command: `rec {label}`.
 *
 * Writes /sdcard/<label>_xiao_<nn>.wav (mic tag xiao, mirroring the
 * dataset/ originals layout: {label}_{mic}_{nn}.wav).
 *
 * Replies on serial:
 *   OK <path>         — recording started
 *   DONE <bytes>      — WAV written, ready for next command
 *   ERR <msg>         — failure (SD, mic, buffer, ...)
 *
 * NOTE: on-device verification (5 s WAV to SD + DONE response) is deferred
 * to issue #10 (T7 — hardware validation). This file is host-reviewed only.
 */

#include <Arduino.h>
#include <SD.h>
#include <SPI.h>
#include <PDM.h>

// ---- Audio format (matches ultron dataset contract: 16 kHz / 16-bit / mono) ----
static constexpr uint32_t SAMPLE_RATE = 16000;
static constexpr uint8_t  CHANNELS    = 1;
static constexpr uint8_t  SAMPLE_BITS = 16;
static constexpr uint32_t RECORD_SECONDS = 5;

// XIAO ESP32S3 Sense built-in PDM mic pins (Seeed studio).
static constexpr int8_t PDM_DATA_PIN = GPIO_NUM_41;
static constexpr int8_t PDM_CLK_PIN  = GPIO_NUM_40;

// SD card (XIAO Sense SD slot, SPI).
static constexpr int8_t SD_CS_PIN = GPIO_NUM_10;

// WAV header size (canonical 44-byte PCM header).
static constexpr size_t WAV_HEADER_SIZE = 44;

// PDM ring buffer (bytes; PDM.available()/read() operate in bytes).
static uint8_t pcm_buffer[8192];
static volatile size_t pcm_fill = 0;
static volatile bool pcm_overflow = false;

static File wav_file;
static uint32_t data_chunk_size = 0;
static bool recording = false;

// PDM ISR callback — called by the PDM driver when a frame is ready.
static void on_pdm_receive() {
  int available = PDM.available();
  if (available <= 0) return;
  if (!recording) {
    uint8_t drain[256];
    while (PDM.available() > 0) PDM.read(drain, sizeof(drain));  // drain while idle
    return;
  }
  size_t space = sizeof(pcm_buffer) - pcm_fill;
  size_t to_read = (size_t)available < space ? (size_t)available : space;
  if (to_read < (size_t)available) pcm_overflow = true;
  PDM.read(pcm_buffer + pcm_fill, to_read);
  pcm_fill += to_read;
}

// ---- WAV helpers (little-endian PCM) ----
static void write_u16(File& f, uint16_t v) { f.write((uint8_t)(v & 0xFF)); f.write((uint8_t)(v >> 8)); }
static void write_u32(File& f, uint32_t v) {
  f.write((uint8_t)(v & 0xFF)); f.write((uint8_t)((v >> 8) & 0xFF));
  f.write((uint8_t)((v >> 16) & 0xFF)); f.write((uint8_t)(v >> 24));
}

static bool write_wav_header(File& f, uint32_t byte_rate, uint32_t data_size) {
  uint32_t chunk_size = 36 + data_size;  // RIFF chunk size
  uint16_t block_align = CHANNELS * (SAMPLE_BITS / 8);
  if (!f.print("RIFF")) return false;
  write_u32(f, chunk_size);
  if (!f.print("WAVE")) return false;
  if (!f.print("fmt ")) return false;
  write_u32(f, 16);                 // subchunk1 size (PCM)
  write_u16(f, 1);                  // audio format PCM
  write_u16(f, CHANNELS);
  write_u32(f, SAMPLE_RATE);
  write_u32(f, byte_rate);          // sample_rate * block_align
  write_u16(f, block_align);
  write_u16(f, SAMPLE_BITS);
  if (!f.print("data")) return false;
  write_u32(f, data_size);
  return true;
}

static uint32_t next_sequence(const char* label) {
  uint32_t max_nn = 0;
  File root = SD.open("/sdcard");
  if (!root) return 1;
  root.rewindDirectory();
  while (true) {
    File entry = root.openNextFile();
    if (!entry) break;
    if (!entry.isDirectory()) {
      const char* name = entry.name();
      char prefix[32];
      snprintf(prefix, sizeof(prefix), "%s_xiao_", label);
      if (strncmp(name, prefix, strlen(prefix)) == 0) {
        const char* suf = name + strlen(prefix);
        while (*suf >= '0' && *suf <= '9') suf++;
        if (strcmp(suf, ".wav") == 0) {
          char* end = nullptr;
          long n = strtol(name + strlen(prefix), &end, 10);
          if (end == suf && n > 0 && (uint32_t)n > max_nn) max_nn = (uint32_t)n;
        }
      }
    }
    entry.close();
  }
  root.close();
  return max_nn + 1;
}

static bool start_recording(const char* label) {
  if (!SD.begin(SD_CS_PIN, SPI, 4000000)) {
    Serial.print(F("ERR sd_begin_failed"));
    return false;
  }
  uint32_t nn = next_sequence(label);
  char path[64];
  snprintf(path, sizeof(path), "/sdcard/%s_xiao_%02u.wav", label, nn);

  wav_file = SD.open(path, FILE_WRITE);
  if (!wav_file) {
    Serial.print(F("ERR sd_open_failed"));
    return false;
  }
  // Placeholder header; fixed up on close.
  uint32_t byte_rate = SAMPLE_RATE * CHANNELS * (SAMPLE_BITS / 8);
  uint32_t expected_samples = SAMPLE_RATE * RECORD_SECONDS;
  uint32_t expected_bytes = expected_samples * CHANNELS * (SAMPLE_BITS / 8);
  if (!write_wav_header(wav_file, byte_rate, expected_bytes)) {
    wav_file.close();
    Serial.print(F("ERR header_write_failed"));
    return false;
  }
  data_chunk_size = 0;
  pcm_fill = 0;
  pcm_overflow = false;
  recording = true;
  if (!PDM.begin(SAMPLE_RATE, CHANNELS)) {
    recording = false;
    wav_file.close();
    Serial.print(F("ERR pdm_begin_failed"));
    return false;
  }
  PDM.onReceive(on_pdm_receive);
  Serial.print(F("OK "));
  Serial.println(path);
  return true;
}

static void flush_buffer() {
  while (pcm_fill > 0 && recording) {
    size_t to_write = pcm_fill;
    if (wav_file) wav_file.write(pcm_buffer, to_write);
    data_chunk_size += to_write;
    pcm_fill = 0;
  }
}

static void stop_recording() {
  recording = false;
  PDM.end();
  flush_buffer();
  if (wav_file) {
    // Fix up the data chunk size in the header.
    uint32_t byte_rate = SAMPLE_RATE * CHANNELS * (SAMPLE_BITS / 8);
    wav_file.seek(0);
    write_wav_header(wav_file, byte_rate, data_chunk_size);
    wav_file.seek(0, SeekEnd);
    wav_file.close();
    Serial.print(F("DONE "));
    Serial.println(data_chunk_size);
  }
}

static void handle_command(const char* cmd) {
  // Expected: "rec <label>"
  if (strncmp(cmd, "rec ", 4) != 0) {
    Serial.println(F("ERR unknown_command"));
    return;
  }
  const char* label = cmd + 4;
  // Trim leading whitespace.
  while (*label == ' ' || *label == '\t') label++;
  char label_buf[32];
  size_t i = 0;
  while (*label && *label != '\r' && *label != '\n' && *label != ' ' && i < sizeof(label_buf) - 1) {
    label_buf[i++] = (char)*label++;
  }
  label_buf[i] = '\0';
  if (label_buf[0] == '\0') {
    Serial.println(F("ERR empty_label"));
    return;
  }
  if (recording) {
    Serial.println(F("ERR already_recording"));
    return;
  }
  start_recording(label_buf);
}

void setup() {
  Serial.begin(115200);
  while (!Serial) { /* wait for serial port */ }
  Serial.println(F("wav_recorder ready — send `rec <label>`"));
}

void loop() {
  static char line[64];
  static size_t len = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (len > 0) {
        line[len] = '\0';
        handle_command(line);
        len = 0;
      }
    } else if (len < sizeof(line) - 1) {
      line[len++] = c;
    }
  }
  if (recording) {
    flush_buffer();
    // Auto-stop after RECORD_SECONDS of audio captured.
    uint32_t target_samples = SAMPLE_RATE * RECORD_SECONDS;
    uint32_t captured_samples = data_chunk_size / (CHANNELS * (SAMPLE_BITS / 8));
    if (captured_samples >= target_samples) {
      stop_recording();
    }
  }
}
