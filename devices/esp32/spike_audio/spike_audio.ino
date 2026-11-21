// spike_audio — hardware-validation SPIKE (spec 003 gates; marks nothing complete)
// Clip mode: streams "Hola, estoy aquí" (Paulina, es-MX) from FLASH via I2S DMA —
// the exact mechanism of FR-018/FR-023. Static chunked writes (lesson: loop() task
// stack is ~8 KB — buffers are never stack-allocated). XIAO ESP32S3 → MAX98357A:
// BCLK=D2(GPIO3), LRC=D3(GPIO4), DIN=D4(GPIO5); GAIN/SD floating, SD→3V3 if silent.

#include <ESP_I2S.h>
#include "hola.h"   // const hola_st_pcm[] + hola_st_pcm_len (16 kHz / stereo / s16le)

constexpr int PIN_I2S_BCLK = 3;   // D2
constexpr int PIN_I2S_LRC  = 4;   // D3
constexpr int PIN_I2S_DIN  = 5;   // D4
constexpr unsigned CHUNK   = 4096;  // bytes per write, static-safe streaming

I2SClass i2s;

void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.printf("Clip: %u bytes (%.2f s)\n", hola_st_pcm_len, hola_st_pcm_len / 64000.0);
  i2s.setPins(PIN_I2S_BCLK, PIN_I2S_LRC, PIN_I2S_DIN);
  if (!i2s.begin(I2S_MODE_STD, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) {
    Serial.println("I2S init FAILED");
    while (true) { delay(1000); }
  }
  Serial.println("I2S OK — playing 'Hola, estoy aquí' every 3 s…");
}

void loop() {
  Serial.println("play: Hola, estoy aquí");
  for (unsigned off = 0; off < hola_st_pcm_len; off += CHUNK) {
    unsigned n = (off + CHUNK <= hola_st_pcm_len) ? CHUNK : (hola_st_pcm_len - off);
    i2s.write(hola_st_pcm + off, n);   // streamed directly from flash
  }
  delay(3000);
}
