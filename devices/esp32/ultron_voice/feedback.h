/*
 * feedback — fixed-clip audio feedback on the XIAO ESP32S3 (spec 003-can-act T14).
 *
 * Covers: FR-018 ("Escuchando" on wake), FR-019 (deterministic trigger + no
 * self-hearing), FR-037 ("comando" on command), NFR-010 (playback budgets),
 * US-007. D-006 / Q-012: the clips are pre-rendered, committed under assets/ and
 * embedded in flash by clips.h — no target synthesizes speech at runtime.
 *
 * Output path (hardware-validated 2026-11-20, devices/esp32/spike_audio):
 *   MAX98357A I2S DAC/amp ← BCLK D2/GPIO3, LRC D3/GPIO4, DIN D4/GPIO5
 *   GAIN + SD floating (9 dB, enabled), amp VIN on 5 V VBUS (Q-014),
 *   speaker 4–8 Ω 3 W. The spike proved the pinout and flash-streamed chunks.
 *
 * Peripheral split: the PDM microphone owns I2S0 (the UltronVoice library,
 * src/pdm_capture.h); the speaker owns I2S1 here. The S3 has exactly two I2S
 * ports, so the port is explicit.
 *
 * Why the player owns its I2S TX channel instead of using I2SClass:
 *   UltronVoice::poll() performs one 1600-sample microphone read per call, which
 *   blocks ~100 ms, and a classification pass on top of that every 500 ms. The
 *   player can therefore only be serviced between polls. I2SClass's TX DMA is
 *   fixed at 6 × 240 frames = 90 ms — shorter than one poll — so a 441 ms clip
 *   would underrun (audible gaps) no matter how it is fed. The player sizes its
 *   own TX queue to 16 × 240 frames = 240 ms and feeds at most 180 ms ahead of
 *   the audible position; the queue always has room, the writes never block the
 *   app loop, and the clip still ends at start + duration.
 *
 * Timing contract with the library: UltronVoice::Options.wakeClipMs /
 * commandClipMs arm the playback interval inside the library's event sink, so
 * CONFIRMING lasts exactly the clip's duration and the 5 s command window starts
 * at playback_end_ms (FR-019/NFR-010). The app only *plays*; it never calls
 * ultron_sm_playback_start() itself. The interval handed to the library is
 * exactly the duration returned by feedback_play() — the committed asset length.
 *
 * Memory: every playback buffer is static and the clip streams from flash — no
 * clip buffer is ever copied into RAM, and no stack buffer exceeds the small
 * chunk. The TX DMA (7.5 KB) is internal RAM and reported by docs/feedback.md.
 *
 * Buffer layout: the clips are mono; the channel runs in I2S_SLOT_MODE_MONO,
 * where the ESP32-S3 transmits the same sample in both slots (hardware
 * `tx_mono` + `tx_chan_equal`), so the MAX98357A in its default (L+R)/2 mode
 * plays full level without a duplication loop.
 */

#ifndef ULTRON_FEEDBACK_H
#define ULTRON_FEEDBACK_H

#include <stdint.h>

namespace ultron {

/* The two fixed confirmation clips (FR-018, FR-037). */
enum FeedbackClip : uint8_t {
  kFeedbackEscuchando = 0, /* wake accepted  → "Escuchando" ≤ 500 ms */
  kFeedbackComando = 1,    /* command emitted → "Comando"    ≤ 300 ms */
};

struct ClipInfo {
  const char *name;        /* DBG play=<name> */
  const int16_t *pcm;      /* flash-resident mono PCM @ 16 kHz */
  uint32_t samples;
  uint32_t duration_ms;
};

/* Metadata for a clip (static storage; never NULL for a valid enum value). */
const ClipInfo *feedback_clip(FeedbackClip clip);

/* Bring up I2S1 → MAX98357A. Returns false on any driver error; the caller must
 * emit EVT ERROR code=SPK_INIT_FAILED and halt (fail-fast, design.md). */
bool feedback_begin();

/* Stop the I2S output and release the channel (symmetry with begin()). */
void feedback_end();

/* Start a clip now. Returns the clip's duration in ms — the value the caller
 * puts in UltronVoice::Options and prints as evidence — or 0 if there is no
 * usable output. Never blocks longer than one small DMA write. */
uint32_t feedback_play(FeedbackClip clip, uint32_t now_ms);

/* Non-blocking pump: call from loop() after voice.poll(). Keeps the clip fed
 * without ever waiting for the whole clip, and logs
 * DBG play=<name> state=end t_ms=… at the audible end. */
void feedback_service(uint32_t now_ms);

/* True while a clip is streaming (from the first byte written to the drained
 * end). Lets a caller refuse to stack two clips on one another. */
bool feedback_playing();

/* I2S port and rate the speaker owns — I2S1 / 16000 Hz (docs/feedback.md). */
int feedback_port();
int feedback_sample_rate();

}  // namespace ultron

#endif /* ULTRON_FEEDBACK_H */
