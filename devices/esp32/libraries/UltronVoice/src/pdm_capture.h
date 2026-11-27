/*
 * pdm_capture — shared PDM microphone capture for the Seeed XIAO ESP32S3 Sense.
 *
 * Origin: spec 003-can-act T12 (firmware core). Extracted as a *shared* module
 * under devices/esp32/common/ so the other XIAO sketches (T14 audio feedback,
 * T28 device-mic recording) reuse one capture path instead of three forks.
 *
 * Why this file exists at all: the ESP32 Arduino core 3.3.12 no longer ships
 * `PDM.h` (the ESP32 2.x API), so `devices/esp32/wav_recorder/wav_recorder.ino`
 * does not compile today. The supported route on 3.3.12 is I2S PDM RX through
 * `ESP_I2S.h` (`I2S_MODE_PDM_RX` + `setPinsPdmRx`), gated on
 * SOC_I2S_SUPPORTS_PDM_RX — which is 1 on the ESP32-S3
 * (soc/esp32s3/soc_caps.h: SOC_I2S_SUPPORTS_PDM_RX, SOC_I2S_PDM_MAX_RX_LINES 4).
 *
 * Peripheral ownership (important — the ESP32-S3 has exactly two I2S ports):
 *   I2S0 (I2S_NUM_0)  ← PDM mic (this module)
 *   I2S1 (I2S_NUM_1)  ← MAX98357A speaker output (T14)
 * The port is a constructor parameter rather than an implicit I2S_NUM_AUTO so
 * the two paths can never fight over one controller. Capture defaults to I2S0;
 * the speaker side (T14) must take I2S1.
 *
 * Pins (XIAO ESP32S3 Sense expansion board, Seeed Studio): PDM data = GPIO41,
 * PDM clock = GPIO40 — the same pins the legacy PDM.h sketch used.
 *
 * Header-only on purpose: arduino-cli only compiles `.c/.cpp` files that live
 * in the sketch folder (or its `src/` subfolder), so a shared implementation
 * file outside the sketch cannot be linked by a plain sketch build. `inline`
 * functions keep a single source of truth that every sketch can `#include`
 * with a relative path (`#include "../common/pdm_capture.h"`).
 *
 * Format: int16 mono @ 16 kHz — the ultron audio contract (spec 001).
 */

#ifndef ULTRON_PDM_CAPTURE_H
#define ULTRON_PDM_CAPTURE_H

#include <Arduino.h>
#include <ESP_I2S.h>

#if !SOC_I2S_SUPPORTS_PDM_RX
#error "This core's SOC has no I2S PDM RX support — XIAO ESP32S3 required (SOC_I2S_SUPPORTS_PDM_RX)."
#endif

namespace ultron {

/* Default capture geometry — the ultron audio contract (16 kHz / 16-bit / mono). */
struct PdmCaptureConfig {
  uint32_t sample_rate = 16000;
  int8_t clk_pin = 40;   /* Sense PDM clock */
  int8_t data_pin = 41;  /* Sense PDM data  */
  i2s_port_t port = I2S_NUM_0;  /* mic owns I2S0; speaker (T14) owns I2S1 */
  /* Read timeout per chunk. PDM RX is a live stream: a timeout means the mic is
   * not delivering samples (wrong pin/board or a dead clock) → the caller turns
   * this into EVT ERROR code=MIC_INIT_FAILED / MIC_READ_FAILED. */
  uint32_t read_timeout_ms = 1000;
};

class PdmCapture {
 public:
  PdmCapture() = default;
  explicit PdmCapture(const PdmCaptureConfig &cfg) : cfg_(cfg), i2s_(cfg.port) {}
  PdmCapture(const PdmCapture &) = delete;
  PdmCapture &operator=(const PdmCapture &) = delete;

  /* Bring the PDM receiver up. Returns false on any driver error — callers must
   * emit EVT ERROR code=MIC_INIT_FAILED and stop (fail loud, never run deaf). */
  bool begin(const PdmCaptureConfig &cfg) {
    cfg_ = cfg;
    return begin();
  }

  bool begin() {
    if (ready_) return true;
    i2s_ = I2SClass(cfg_.port);
    /* S3 PDM RX wants a 16-bit slot width; the mono slot layout delivers one
     * int16 sample per frame (no stereo duplication to undo). */
    i2s_.setPinsPdmRx(cfg_.clk_pin, cfg_.data_pin);
    if (!i2s_.begin(I2S_MODE_PDM_RX, cfg_.sample_rate, I2S_DATA_BIT_WIDTH_16BIT,
                    I2S_SLOT_MODE_MONO)) {
      ready_ = false;
      return false;
    }
    ready_ = true;
    return true;
  }

  void end() {
    if (!ready_) return;
    i2s_.end();
    ready_ = false;
  }

  /* Blocking read of up to `samples` int16 mono samples.
   * Returns the number of samples actually read (0 on timeout/error). Callers
   * read in chunks (e.g. 100 ms) so the main loop keeps servicing the state
   * machine and the serial sink. */
  size_t read(int16_t *dst, size_t samples) {
    if (!ready_ || dst == nullptr || samples == 0) return 0;
    size_t bytes = samples * sizeof(int16_t);
    size_t got = i2s_.readBytes(reinterpret_cast<char *>(dst), bytes);
    return got / sizeof(int16_t);
  }

  bool ready() const { return ready_; }
  uint32_t sampleRate() const { return cfg_.sample_rate; }
  int8_t clockPin() const { return cfg_.clk_pin; }
  int8_t dataPin() const { return cfg_.data_pin; }
  i2s_port_t port() const { return cfg_.port; }

 private:
  PdmCaptureConfig cfg_{};
  I2SClass i2s_{I2S_NUM_0};
  bool ready_ = false;
};

}  // namespace ultron

#endif /* ULTRON_PDM_CAPTURE_H */
