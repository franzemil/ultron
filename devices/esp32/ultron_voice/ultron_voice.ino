/*
 * ultron_voice — application sketch (T12: console-only firmware core).
 * Seeed XIAO ESP32S3 Sense · spec 003-can-act.
 *
 * Everything voice-related lives in the **UltronVoice** library
 * (devices/esp32/libraries/UltronVoice): PDM capture, MFCC, TFLite-Micro, thresholds,
 * the WAKE_LISTEN/CONFIRMING/LISTENING machine and the Target Event Contract lines
 * (`EVT WAKE / CMD / TIMEOUT / ERROR` + one `DBG pred=` line per window).
 *
 * This file is app glue only — it must not grow a voice pipeline, a pin map or a
 * channel contract:
 *
 *   T12 (here)  : bring the library up, pump poll(), hand events to the app hooks.
 *   T15 (#40)   : the relay layer — devices/esp32/ultron_voice/outputs.{h,cpp}
 *                 (all seven Actuation Behavior Contract behaviours behind a ChannelSink)
 *                 and pins.h (the pin map, with wired/unwired per channel). Those are the
 *                 single source of truth for FR-009 / FR-017 / NFR-009; wire them into
 *                 onCommand() below and forward the engine's DBG lines to voice.console().
 *   T14 (#41)   : audio feedback — set Options.wakeClipMs / commandClipMs and play the clip
 *                 on the Wake/Command events (the library already owns the overlap timing).
 *
 * Build/flash: uv run ultron esp32 deploy [--selftest] [--listen 45]
 * Expected console: boot banner, then `DBG pred=…` every 500 ms, and `EVT …` lines when a
 * gate is crossed. With the placeholder model no `EVT` line can appear (see PR #38).
 */

#include <Arduino.h>

#include <UltronVoice.h>

namespace {

UltronVoice voice;

/* ── application hooks ───────────────────────────────────────────────────────
 * Deliberately empty in T12: this build has no outputs at all (console-only). They
 * exist so the downstream layers have exactly one place to plug in and this file
 * never needs to learn about relays, pins or clips.
 * ------------------------------------------------------------------------- */

/* T15 seam — relay layer (FR-009 / FR-017 / NFR-009). Do not implement it here:
 * construct T15's channel sink over the GPIOs from pins.h, call its engine with
 * `ev.id`, and forward the engine's `DBG out=` lines to voice.console(). */
void onCommand(const UltronVoice::Event &ev) {
  (void)ev;
}

/* T14 seam — audio feedback (FR-018 / FR-019 / FR-037 / NFR-010). Play the fixed
 * clip here; the library has already armed the playback interval on the same edge. */
void onWake(const UltronVoice::Event &ev) {
  (void)ev;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  /* Give the host ~2 s to attach, but never block boot on it (headless OK). */
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 2000) {
    delay(10);
  }

  UltronVoice::Options options;
#ifdef ULTRON_SELFTEST
  options.selftest = true; /* scripted scores: proves the contract, never recognition */
#endif
  voice.begin(options); /* prints the boot banner; EVT ERROR + halt on any fault */
}

void loop() {
  UltronVoice::Event ev;
  if (!voice.poll(ev)) return;

  switch (ev.type) {
    case UltronVoice::Type::Wake:
      onWake(ev);
      break;
    case UltronVoice::Type::Command:
      onCommand(ev);
      break;
    case UltronVoice::Type::Timeout:
      /* Window closed with nothing recognised; the luz state (T15) stays as it is. */
      break;
    case UltronVoice::Type::Error:
      /* The library already printed the EVT ERROR line and halted. Outputs are T15's:
       * they must fail safe there (all channels OFF), not in this file. */
      break;
  }
}
