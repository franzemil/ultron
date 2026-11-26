/*
 * feedback — implementation (spec 003-can-act T14, see feedback.h for the design).
 *
 * No allocation, no runtime speech synthesis and no blocking wait for a whole
 * clip: the two clips live in flash (clips.h) and are streamed to the MAX98357A
 * through I2S1 in static 480-byte chunks.
 */

#include "feedback.h"

#include <Arduino.h>
#include <string.h>

#include "driver/i2s_std.h"

#include "clips.h"

namespace ultron {
namespace {

/* ── wiring (validated on hardware 2026-11-20, devices/esp32/spike_audio) ── */
constexpr gpio_num_t kPinBclk = GPIO_NUM_3; /* D2 → MAX98357A BCLK */
constexpr gpio_num_t kPinLrc = GPIO_NUM_4;  /* D3 → MAX98357A LRC  */
constexpr gpio_num_t kPinDin = GPIO_NUM_5;  /* D4 → MAX98357A DIN  */

/* One DMA descriptor holds 240 frames = 15 ms at 16 kHz. */
constexpr uint32_t kDmaFrameNum = 240;
/* 16 × 15 ms = 240 ms of TX queue. Sized to outlast one UltronVoice::poll()
 * (~100 ms microphone read) plus a classification pass (~win_ms), so playback
 * never underruns while the app loop is busy. The ESP_I2S default (6 × 240 =
 * 90 ms) is shorter than a single poll — that is why this channel is created
 * directly with the IDF driver. */
constexpr uint32_t kDmaDescNum = 16;
/* How far ahead of the audible position the clip is fed. < the 240 ms queue and
 * > one poll, so the end of the clip stays aligned with start + duration (the
 * queue drains while the cursor is already finished). */
constexpr uint32_t kFeedLeadMs = 180;
/* Per-write driver timeout; the pump never fills the queue, so this is only a
 * liveness guard. */
constexpr uint32_t kWriteTimeoutMs = 100;

/* 16 samples per millisecond — the ultron audio contract. */
constexpr uint32_t kSamplesPerMs = ULTRON_CLIP_SAMPLE_RATE / 1000u;
/* Guard against millis() wrap nonsense in the feed calculation. */
constexpr uint32_t kMaxElapsedMs = 5000;

const ClipInfo kClips[2] = {
    {"escuchando", ULTRON_CLIP_ESCUCHANDO, ULTRON_CLIP_ESCUCHANDO_SAMPLES,
     ULTRON_CLIP_ESCUCHANDO_MS},
    {"comando", ULTRON_CLIP_COMANDO, ULTRON_CLIP_COMANDO_SAMPLES, ULTRON_CLIP_COMANDO_MS},
};

/* NFR-010 encoded as a compile-time contract: a re-rendered asset that blows the
 * budget cannot even build. */
static_assert(ULTRON_CLIP_ESCUCHANDO_SAMPLES / kSamplesPerMs <= 500u,
              "escuchando.wav exceeds the 500 ms budget (NFR-010)");
static_assert(ULTRON_CLIP_COMANDO_SAMPLES / kSamplesPerMs <= 300u,
              "comando.wav exceeds the 300 ms budget (NFR-010)");

i2s_chan_handle_t s_tx = nullptr; /* I2S1: the PDM microphone owns I2S0 */

/* Static, never stacked: the loop() task stack is small (design.md). */
int16_t s_chunk[kDmaFrameNum];

struct PlayerState {
  bool ready;             /* I2S1 initialized */
  bool playing;           /* first byte queued, audible tail not drained yet */
  const ClipInfo *clip;
  uint32_t start_ms;      /* millis() at the first byte */
  uint32_t cursor;        /* mono samples handed to the driver */
  uint32_t last_write_ms; /* millis() when the last sample was queued */
};

PlayerState s_player = {};

void logPlay(const char *state, uint32_t now_ms) {
  char buf[64];
  snprintf(buf, sizeof(buf), "DBG play=%s state=%s t_ms=%lu",
           s_player.clip != nullptr ? s_player.clip->name : "?", state,
           (unsigned long)now_ms);
  /* No Serial.flush(): HWCDC::flush() can spin for up to 100 ms, which would
   * sit inside the playback pump and the app loop (same rule as the library). */
  Serial.println(buf);
}

}  // namespace

const ClipInfo *feedback_clip(FeedbackClip clip) {
  const uint8_t index = static_cast<uint8_t>(clip);
  if (index >= sizeof(kClips) / sizeof(kClips[0])) return &kClips[0];
  return &kClips[index];
}

bool feedback_begin() {
  if (s_player.ready) return true;
#ifdef ULTRON_FAULT_NO_SPK
  /* Bench fault injection (docs/feedback.md): prove the boot path emits
   * EVT ERROR code=SPK_INIT_FAILED when the output device is unusable. */
  Serial.println("DBG spk_init=forced_failure ULTRON_FAULT_NO_SPK=1");
#endif
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
  chan_cfg.dma_desc_num = kDmaDescNum;
  chan_cfg.dma_frame_num = kDmaFrameNum;
  chan_cfg.auto_clear_after_cb = true; /* an underrun outputs silence, never a loop */

  if (i2s_new_channel(&chan_cfg, &s_tx, nullptr) != ESP_OK) {
    s_tx = nullptr;
    return false;
  }

  i2s_std_config_t std_cfg = {
      .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(ULTRON_CLIP_SAMPLE_RATE),
      /* Mono: the S3 sends the same sample in both slots (tx_mono +
       * tx_chan_equal), so the amp's default (L+R)/2 mode plays full level. */
      .slot_cfg =
          I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
      .gpio_cfg =
          {
              .mclk = I2S_GPIO_UNUSED,
              .bclk = kPinBclk,
              .ws = kPinLrc,
              .dout = kPinDin,
              .din = I2S_GPIO_UNUSED,
              .invert_flags = {.mclk_inv = false, .bclk_inv = false, .ws_inv = false},
          },
  };

  if (i2s_channel_init_std_mode(s_tx, &std_cfg) != ESP_OK) {
    i2s_del_channel(s_tx);
    s_tx = nullptr;
    return false;
  }
  if (i2s_channel_enable(s_tx) != ESP_OK) {
    i2s_channel_disable(s_tx);
    i2s_del_channel(s_tx);
    s_tx = nullptr;
    return false;
  }
  s_player.ready = true;
  return true;
}

void feedback_end() {
  if (!s_player.ready || s_tx == nullptr) return;
  s_player.playing = false;
  i2s_channel_disable(s_tx);
  i2s_del_channel(s_tx);
  s_tx = nullptr;
  s_player.ready = false;
}

uint32_t feedback_play(FeedbackClip clip, uint32_t now_ms) {
  if (!s_player.ready || s_tx == nullptr) return 0;
  const ClipInfo *info = feedback_clip(clip);
  if (info->samples == 0) return 0;

  /* A clip may only start from silence, and the caller decides whether the
   * previous one finished; a restart simply requeues from sample 0. */
  s_player.playing = true;
  s_player.clip = info;
  s_player.start_ms = now_ms;
  s_player.cursor = 0;
  s_player.last_write_ms = now_ms;
  logPlay("start", now_ms);

  /* Prime the queue immediately: the first chunk is audible within one DMA
   * descriptor (15 ms), well inside FR-019's / FR-037's 200 ms start budget. */
  feedback_service(now_ms);
  return info->duration_ms;
}

void feedback_service(uint32_t now_ms) {
  if (!s_player.playing || s_player.clip == nullptr || s_tx == nullptr) return;

  /* Audible position + feed lead, capped at the clip length: the driver is
   * never handed more than kFeedLeadMs of audio, so the last sample is played
   * at start + duration and the app loop never waits for the whole clip. */
  uint32_t elapsed = now_ms - s_player.start_ms;
  if (elapsed > kMaxElapsedMs) elapsed = kMaxElapsedMs;
  uint32_t target = elapsed * kSamplesPerMs + kFeedLeadMs * kSamplesPerMs;
  const uint32_t total = s_player.clip->samples;
  if (target > total) target = total;

  while (s_player.cursor < target) {
    uint32_t frames = total - s_player.cursor;
    if (frames > kDmaFrameNum) frames = kDmaFrameNum;
    memcpy(s_chunk, s_player.clip->pcm + s_player.cursor, frames * sizeof(int16_t));
    const size_t want = frames * sizeof(int16_t);
    size_t written = 0;
    const esp_err_t err = i2s_channel_write(s_tx, s_chunk, want, &written, kWriteTimeoutMs);
    if (written == 0) {
      /* Driver timeout/error with no progress mid-clip: stop cleanly. Boot
       * already proved the device works (SPK_INIT_FAILED otherwise). */
      logPlay("abort", now_ms);
      s_player.playing = false;
      return;
    }
    s_player.cursor += written / sizeof(int16_t);
    s_player.last_write_ms = now_ms;
    if (err != ESP_OK) {
      /* Partial write kept; the next service call resumes from the cursor. */
      break;
    }
  }

  /* The last sample was queued kFeedLeadMs ago → the audible tail has drained. */
  if (s_player.cursor >= total &&
      (uint32_t)(now_ms - s_player.last_write_ms) >= kFeedLeadMs) {
    logPlay("end", now_ms);
    s_player.playing = false;
  }
}

bool feedback_playing() { return s_player.playing; }

int feedback_port() { return static_cast<int>(I2S_NUM_1); }

int feedback_sample_rate() { return static_cast<int>(ULTRON_CLIP_SAMPLE_RATE); }

}  // namespace ultron
