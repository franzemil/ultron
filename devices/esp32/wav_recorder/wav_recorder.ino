/*
 * wav_recorder — Seeed XIAO ESP32S3 Sense
 *
 * Ultron T4/T28: records a 16 kHz / 16-bit / mono WAV clip from the built-in
 * PDM microphone to the SD card, labeled by phrase, for training data
 * (US-002 / T4 / T28).
 *
 * CAPTURE PATH (T28): the ESP32 Arduino core 3.x removed the old `PDM.h`
 * driver (it existed in core 2.0.x only), so this sketch captures through
 * the core's `ESP_I2S` library in PDM RX mode — the same peripheral the
 * removed driver used, with no extra dependency and no toolchain downgrade:
 *
 *     i2s.setPinsPdmRx(clk = GPIO40, din0 = GPIO41);
 *     i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
 *     i2s.readBytes((char *)buf, bytes);   // int16 little-endian, mono, 16 kHz
 *
 * ESP32-S3 supports PDM RX (`SOC_I2S_SUPPORTS_PDM_RX = 1`) and returns
 * already-decimated 16-bit PCM, which is exactly the dataset contract. The
 * firmware core (T12) needs the same capture path — see docs/recording.md.
 *
 * Serial protocol (115200-8N1 or any baud: the S3 USB-Serial-JTAG CDC ignores
 * the line rate):
 *   rec {label}       record 5 s → /sdcard/{label}_xiao_{nn}.wav
 *                     replies  OK <path>  then  DONE <bytes>
 *                              (DBG peak=<n> mean=<n> carries the level/DC
 *                               evidence used by US-002 AC3)
 *                     or       ERR <msg>
 *   ls                list the card:  LIST <name> <bytes>  …  LISTDONE <count>
 *   get {name}        dump a file:    BEGIN <name> <bytes>\n<raw bytes>\nEND\n
 *   help              print this protocol
 *
 * The SD library mounts the FAT volume at "/sdcard", so file paths handed to
 * SD.open() are relative to that mount ("/<name>.wav") while the path printed
 * on serial carries the mountpoint ("/sdcard/<name>.wav") for humans.
 */

#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include "ESP_I2S.h"

// ---- Audio format (matches the ultron dataset contract: 16 kHz / 16-bit / mono) ----
static constexpr uint32_t SAMPLE_RATE = 16000;
static constexpr uint8_t CHANNELS = 1;
static constexpr uint8_t SAMPLE_BITS = 16;
static constexpr uint32_t RECORD_SECONDS = 5;
static constexpr uint32_t BYTES_PER_SAMPLE = (SAMPLE_BITS / 8) * CHANNELS;

// One DMA-sized chunk: 512 samples = 1024 bytes = 32 ms @ 16 kHz.
static constexpr size_t CHUNK_SAMPLES = 512;
static constexpr size_t CHUNK_BYTES = CHUNK_SAMPLES * BYTES_PER_SAMPLE;

// XIAO ESP32S3 Sense built-in PDM mic (Seeed studio expansion board).
static constexpr int8_t PDM_DATA_PIN = 41;
static constexpr int8_t PDM_CLK_PIN = 40;

// XIAO Sense microSD slot (SPI; the card is the only SPI device).
static constexpr int8_t SD_CS_PIN = 10;
static constexpr uint32_t SD_FREQ = 20000000;

// Canonical 44-byte PCM WAV header.
static constexpr size_t WAV_HEADER_SIZE = 44;

static I2SClass i2s;
static File wav_file;
static bool sd_mounted = false;

static uint8_t io_buf[CHUNK_BYTES];
// ---- WAV helpers (little-endian PCM) ----
static void write_u16(File &f, uint16_t v) {
  f.write((uint8_t)(v & 0xFF));
  f.write((uint8_t)(v >> 8));
}
static void write_u32(File &f, uint32_t v) {
  f.write((uint8_t)(v & 0xFF));
  f.write((uint8_t)((v >> 8) & 0xFF));
  f.write((uint8_t)((v >> 16) & 0xFF));
  f.write((uint8_t)(v >> 24));
}

static bool write_wav_header(File &f, uint32_t data_size) {
  const uint16_t block_align = BYTES_PER_SAMPLE;
  const uint32_t byte_rate = SAMPLE_RATE * block_align;
  if (f.print("RIFF") == 0) return false;
  write_u32(f, 36 + data_size);
  if (f.print("WAVE") == 0) return false;
  if (f.print("fmt ") == 0) return false;
  write_u32(f, 16);       // PCM subchunk size
  write_u16(f, 1);        // format = PCM
  write_u16(f, CHANNELS);
  write_u32(f, SAMPLE_RATE);
  write_u32(f, byte_rate);
  write_u16(f, block_align);
  write_u16(f, SAMPLE_BITS);
  if (f.print("data") == 0) return false;
  write_u32(f, data_size);
  return f.position() == WAV_HEADER_SIZE;
}

static bool ensure_sd() {
  if (sd_mounted) return true;
  SPI.begin();
  if (!SD.begin(SD_CS_PIN, SPI, SD_FREQ)) {
    Serial.println(F("ERR sd_begin_failed"));
    return false;
  }
  sd_mounted = true;
  return true;
}

// Next free two-digit sequence number for <label>_xiao_<nn>.wav.
static uint32_t next_sequence(const char *label) {
  uint32_t max_nn = 0;
  File root = SD.open("/");
  if (!root) return 1;
  root.rewindDirectory();
  char prefix[40];
  snprintf(prefix, sizeof(prefix), "%s_xiao_", label);
  const size_t prefix_len = strlen(prefix);
  while (true) {
    File entry = root.openNextFile();
    if (!entry) break;
    if (!entry.isDirectory()) {
      const char *name = entry.name();
      if (strncmp(name, prefix, prefix_len) == 0) {
        char *end = nullptr;
        long n = strtol(name + prefix_len, &end, 10);
        if (end && strcmp(end, ".wav") == 0 && n > 0 && (uint32_t)n > max_nn) {
          max_nn = (uint32_t)n;
        }
      }
    }
    entry.close();
  }
  root.close();
  return max_nn + 1;
}

static bool valid_label(const char *label) {
  const size_t len = strlen(label);
  if (len == 0 || len > 30) return false;
  for (size_t i = 0; i < len; i++) {
    const char c = label[i];
    const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
    if (!ok) return false;
  }
  return true;
}

// ---- capture ----
static void do_record(const char *label) {
  if (!ensure_sd()) return;

  const uint32_t nn = next_sequence(label);
  char fs_path[64];
  snprintf(fs_path, sizeof(fs_path), "/%s_xiao_%02u.wav", label, nn);

  const uint32_t target_bytes = SAMPLE_RATE * RECORD_SECONDS * BYTES_PER_SAMPLE;

  wav_file = SD.open(fs_path, FILE_WRITE);
  if (!wav_file) {
    Serial.println(F("ERR sd_open_failed"));
    return;
  }
  // Placeholder header (data size fixed up when the take closes).
  if (!write_wav_header(wav_file, target_bytes)) {
    wav_file.close();
    Serial.println(F("ERR header_write_failed"));
    return;
  }

  i2s.setPinsPdmRx(PDM_CLK_PIN, PDM_DATA_PIN);
  if (!i2s.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    wav_file.close();
    Serial.println(F("ERR mic_begin_failed"));
    return;
  }

  Serial.print(F("OK /sdcard"));
  Serial.println(fs_path);

  uint32_t written = 0;
  int32_t peak = 0;
  int64_t sum = 0;
  uint32_t samples = 0;
  bool io_error = false;

  while (written < target_bytes) {
    size_t want = target_bytes - written;
    if (want > CHUNK_BYTES) want = CHUNK_BYTES;
    const size_t got = i2s.readBytes((char *)io_buf, want);
    if (got == 0) {  // mic produced nothing before the read timeout
      io_error = true;
      break;
    }
    if (wav_file.write(io_buf, got) != got) {
      io_error = true;
      break;
    }
    written += got;
    const size_t got_samples = got / BYTES_PER_SAMPLE;
    for (size_t i = 0; i < got_samples; i++) {
      const int16_t v = (int16_t)((uint16_t)io_buf[2 * i] | ((uint16_t)io_buf[2 * i + 1] << 8));
      const int32_t mag = v < 0 ? -(int32_t)v : (int32_t)v;
      if (mag > peak) peak = mag;
      sum += v;
    }
    samples += got_samples;
  }

  i2s.end();

  // Fix up the RIFF/data sizes for what was actually captured.
  wav_file.seek(0);
  write_wav_header(wav_file, written);
  wav_file.seek(0, SeekEnd);
  wav_file.close();

  if (io_error) {
    Serial.printf("ERR short_record %u\n", (unsigned)written);
    return;
  }
  Serial.printf("DONE %u\n", (unsigned)written);
  // US-002 AC3 evidence: the PDM path can carry a DC offset; report it so the
  // host can run `ultron dataset dcfix` (mean is the offset to subtract).
  const int32_t mean = samples ? (int32_t)(sum / (int64_t)samples) : 0;
  Serial.printf("DBG peak=%ld mean=%ld samples=%u\n", (long)peak, (long)mean, (unsigned)samples);
}

// ---- bring-up diagnostics ----
// `mic` starts the PDM path, reads 200 ms and reports the level. It needs no SD
// card, so it separates "mic broken" from "card missing" in one round trip.
static void do_mic_probe() {
  i2s.setPinsPdmRx(PDM_CLK_PIN, PDM_DATA_PIN);
  if (!i2s.begin(I2S_MODE_PDM_RX, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println(F("ERR mic_begin_failed"));
    return;
  }
  int32_t peak = 0;
  int64_t sum = 0;
  uint32_t samples = 0;
  const size_t want = (SAMPLE_RATE / 5) * BYTES_PER_SAMPLE;  // 200 ms
  while (samples * BYTES_PER_SAMPLE < want) {
    const size_t got = i2s.readBytes((char *)io_buf, sizeof(io_buf));
    if (got == 0) break;
    const size_t got_samples = got / BYTES_PER_SAMPLE;
    for (size_t i = 0; i < got_samples; i++) {
      const int16_t v = (int16_t)((uint16_t)io_buf[2 * i] | ((uint16_t)io_buf[2 * i + 1] << 8));
      const int32_t mag = v < 0 ? -(int32_t)v : (int32_t)v;
      if (mag > peak) peak = mag;
      sum += v;
    }
    samples += got_samples;
  }
  i2s.end();
  const int32_t mean = samples ? (int32_t)(sum / (int64_t)samples) : 0;
  Serial.printf("MIC peak=%ld mean=%ld samples=%u\n", (long)peak, (long)mean, (unsigned)samples);
}

// ---- retrieval (no card reader needed: pull the WAVs over the same USB port) ----
static void do_list() {
  if (!ensure_sd()) return;
  File root = SD.open("/");
  if (!root) {
    Serial.println(F("ERR sd_open_failed"));
    return;
  }
  root.rewindDirectory();
  uint32_t count = 0;
  while (true) {
    File entry = root.openNextFile();
    if (!entry) break;
    if (!entry.isDirectory()) {
      Serial.printf("LIST %s %u\n", entry.name(), (unsigned)entry.size());
      count++;
    }
    entry.close();
  }
  root.close();
  Serial.printf("LISTDONE %u\n", (unsigned)count);
}

static void do_get(const char *name) {
  if (!ensure_sd()) return;
  // Accept "x.wav", "/x.wav" or "/sdcard/x.wav".
  if (strncmp(name, "/sdcard/", 8) == 0) name += 8;
  while (*name == '/') name++;
  if (*name == '\0') {
    Serial.println(F("ERR empty_name"));
    return;
  }
  char fs_path[64];
  snprintf(fs_path, sizeof(fs_path), "/%s", name);
  File f = SD.open(fs_path, FILE_READ);
  if (!f) {
    Serial.println(F("ERR not_found"));
    return;
  }
  const uint32_t size = f.size();
  Serial.printf("BEGIN %s %u\n", name, (unsigned)size);
  uint32_t sent = 0;
  while (sent < size) {
    size_t want = size - sent;
    if (want > sizeof(io_buf)) want = sizeof(io_buf);
    const size_t got = f.read(io_buf, want);
    if (got == 0) break;
    Serial.write(io_buf, got);
    sent += got;
  }
  f.close();
  Serial.print(F("\nEND\n"));
  Serial.flush();
}

static void print_help() {
  Serial.println(F("commands: rec <label> | ls | get <name> | mic | help"));
}

// ---- command handling ----
static void handle_command(char *line) {
  while (*line == ' ' || *line == '\t') line++;
  if (*line == '\0') return;

  if (strcmp(line, "help") == 0) {
    print_help();
    return;
  }
  if (strcmp(line, "ls") == 0) {
    do_list();
    return;
  }
  if (strcmp(line, "mic") == 0) {
    do_mic_probe();
    return;
  }
  if (strncmp(line, "get ", 4) == 0) {
    do_get(line + 4);
    return;
  }
  if (strncmp(line, "rec ", 4) == 0) {
    char label[32];
    size_t i = 0;
    line += 4;
    while (*line == ' ' || *line == '\t') line++;
    while (*line && *line != ' ' && *line != '\t' && i < sizeof(label) - 1) label[i++] = *line++;
    label[i] = '\0';
    if (!valid_label(label)) {
      Serial.println(F("ERR invalid_label"));
      return;
    }
    do_record(label);
    return;
  }
  Serial.println(F("ERR unknown_command"));
}

void setup() {
  Serial.begin(115200);
  // Don't block boot on a host: the recorder must answer as soon as USB is up.
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 3000) delay(10);
  delay(50);
  Serial.println(F("wav_recorder ready (PDM RX via ESP_I2S) — send `rec <label>`, `ls`, `get <name>`, `mic`"));
  if (ensure_sd()) {
    Serial.println(F("DBG sd=mounted"));
  }
}

void loop() {
  static char line[72];
  static size_t len = 0;
  while (Serial.available()) {
    const char c = (char)Serial.read();
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
  delay(2);
}
