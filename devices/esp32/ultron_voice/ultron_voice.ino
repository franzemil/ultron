/*
 * ultron_voice — application sketch (T12: console-only firmware core).
 * Seeed XIAO ESP32S3 Sense · spec 003-can-act.
 *
 * Everything voice-related lives in the **UltronVoice** library
 * (devices/esp32/libraries/UltronVoice): PDM capture, MFCC, TFLite-Micro, thresholds,
 * the WAKE_LISTEN/CONFIRMING/LISTENING machine, the Target Event Contract lines
 * (`EVT WAKE / CMD / TIMEOUT / ERROR` + one `DBG pred=` line per window) and the
 * clip-arming timing (Options.wakeClipMs / commandClipMs → FR-019/NFR-010).
 *
 * This file is app glue only — it must not grow a voice pipeline, a pin map or a
 * channel contract:
 *
 *   T12 (here)  : bring the library up, pump poll(), hand events to the app hooks.
 *   T15 (#40)   : the relay layer — devices/esp32/ultron_voice/outputs.{h,cpp}
 *                 (all seven Actuation Behavior Contract behaviours behind a ChannelSink)
 *                 and pins.h (the pin map, with wired/unwired per channel). Those are the
 *                 single source of truth for FR-009 / FR-017 / NFR-009; onCommand() below
 *                 only binds them to the console through relay_gpio.h.
 *   T14 (#41)   : audio feedback — feedback.{h,cpp} plays the committed clips on the
 *                 Wake/Command events over I2S1; this sketch boots the player
 *                 (fail-fast on SPK_INIT_FAILED), arms the library with the clips'
 *                 real durations and services the player from loop().
 *
 * Implemented here: T12 (library bring-up), T15 (the relay binding) and T14
 * (speaker boot + the two confirmation clips).
 *
 * Build/flash: ./scripts/deploy_xiao.sh [--selftest] [--listen 45]
 * Expected console: boot banner, `DBG audio …` (T14), `DBG out_pins` / `DBG out_registry`
 * / `DBG out=… state=off` (T15), then `DBG pred=…` every 500 ms and `EVT …` lines when a
 * gate is crossed, each accepted wake/command followed by `DBG play=… state=start|end`.
 * With the placeholder model no `EVT` line can appear (see PR #38).
 */

#include <Arduino.h>

#include <UltronVoice.h>

#include "feedback.h"      /* T14: fixed clips → I2S1 → MAX98357A               */
#include "outputs.h"        /* T15: behaviour engine (channel ids, no hardware)   */
#include "outputs_config.h" /* T15: generated registry → engine command table     */
#include "relay_gpio.h"     /* T15: pin map (pins.h) + GPIO sink + 50 ms tick    */

namespace {

UltronVoice voice;

/* ── T15 — relay layer binding ───────────────────────────────────────────────
 * Engine (outputs.*), pin map (pins.h) and the GPIO/tick/console adapter
 * (relay_gpio.h) do the work. All this block knows is *when* a command arrives
 * and where the console is; no pins, no channel contract, no timing constants.
 * ------------------------------------------------------------------------- */

ultron::RegistryCommandTable g_output_registry; /* config ← model/registry.json */
ultron::RelayGpioSink g_relays;                 /* active-LOW GPIOs from pins.h */
ultron::OutputController g_outputs;             /* FR-009 / FR-017 / NFR-009     */
ultron::OutputTicker g_output_ticker;           /* 50 ms tick (esp_timer)        */
bool g_outputs_ready = false;
uint32_t g_last_tick_stats_ms = 0;

void outputsBegin() {
  ultron::loadRegistryTable(&g_output_registry);
  g_outputs.begin(g_output_registry.table, &g_relays, millis());
  g_outputs_ready = true;

  /* NFR-009 / NFR-008 boot proof: the pin map, the registry binding and the
   * explicit OFF every wired channel starts in. */
  g_relays.logBootState(voice.console(), millis());
  ultron::logPinMap(voice.console());
  ultron::logRegistry(g_output_registry, voice.console());

  /* FR-009: the tick runs on its own 50 ms timer so a blocking microphone read
   * can never delay a relay edge. */
  if (!g_output_ticker.begin(&g_outputs)) {
    voice.console().println("DBG out_ticker=FAILED outputs=untimed");
  }
}

/* Publish the edges the tick produced (and, every 20 s, the measured cadence). */
void outputsPoll() {
  const uint32_t now = millis();
  ultron::logOutputEdges(g_outputs, g_output_ticker, voice.console());
  if (g_last_tick_stats_ms == 0) g_last_tick_stats_ms = now == 0 ? 1 : now;
  if ((uint32_t)(now - g_last_tick_stats_ms) >= 20000) {
    ultron::logTickStats(g_output_ticker, g_outputs, voice.console(), now);
    g_last_tick_stats_ms = now;
  }
}

/* ── application hooks ───────────────────────────────────────────────────────
 * T12 created these empty seams so every downstream layer has exactly one place
 * to plug in. onCommand() below binds T15's engine; the pin map, the channel
 * contract and the timings live in pins.h / outputs.* / relay_gpio.h.
 * ------------------------------------------------------------------------- */

/* T14 — audio feedback (FR-018 / FR-019 / FR-037 / NFR-010) ──────────────────
 * The library owns the timing (Options.wakeClipMs / commandClipMs arm the
 * playback interval on the event edge, so CONFIRMING lasts the clip and the 5 s
 * window starts at playback end). This layer owns the player and the fail-fast
 * boot check. Nothing here blocks: feedback_service() feeds a bounded lead.
 * ------------------------------------------------------------------------- */

bool g_halted = false; /* SPK_INIT_FAILED: a mute device must not pretend to listen */

void audioBegin() {
  if (!ultron::feedback_begin()) {
    /* Same contract text the library's own fail() path produces (design.md →
     * "Audio feedback": fail-fast, Q-003 precedent). */
    voice.console().println("DBG spk_init=0 i2s=1 pins=3/4/5");
    voice.console().println("EVT ERROR code=SPK_INIT_FAILED");
    voice.console().println("DBG halted=1 — reboot to retry");
    g_halted = true;
    return;
  }
  voice.console().println("DBG spk_init=1 i2s=1 pins=3/4/5 gain=floating sd=floating vin=5v_usb");
  /* FR-018 / FR-037: the clips are fixed assets, identical for every target. */
  for (int i = 0; i < 2; i++) {
    const ultron::ClipInfo *clip = ultron::feedback_clip(
        i == 0 ? ultron::kFeedbackEscuchando : ultron::kFeedbackComando);
    voice.console().printf("DBG audio clip=%s samples=%lu ms=%lu budget_ms=%d rate=%d\r\n",
                           clip->name, (unsigned long)clip->samples,
                           (unsigned long)clip->duration_ms, i == 0 ? 500 : 300,
                           ultron::feedback_sample_rate());
  }
}

/* T15 seam — relay layer (FR-009 / FR-017 / NFR-009). */
void onCommand(const UltronVoice::Event &ev) {
  /* T14 (FR-037): speak "Comando" on the same edge. ev.tMs is the acceptance
   * timestamp the library armed the discard interval with — play on that clock so
   * the clip's audible end matches playback_end_ms. */
  ultron::feedback_play(ultron::kFeedbackComando, ev.tMs);

  if (!g_outputs_ready || ev.id == nullptr) return;

  const uint32_t now = millis();
  bool acted = false;
  unsigned mask = 0;
  g_output_ticker.locked([&] {
    acted = g_outputs.onCommand(ev.id, now);
    mask = (unsigned)g_outputs.onMask();
  });
  const ultron::CommandSpec *spec = g_output_registry.table.find(ev.id);
  outputsPoll(); /* the DBG out= edges first, then the command summary */
  voice.console().printf("DBG outcmd id=%s behavior=%s acted=%d on=0x%02x t_ms=%lu\r\n", ev.id,
                         spec != nullptr ? ultron::behaviorName(spec->behavior) : "unknown",
                         acted ? 1 : 0, mask, (unsigned long)now);
}

/* T14 seam — audio feedback (FR-018 / FR-019 / FR-037 / NFR-010). The library
 * already armed the playback interval on this same edge (Options.wakeClipMs), so
 * this only starts the audio — on ev.tMs, the timestamp the interval was armed
 * with, never a fresh millis() reading. */
void onWake(const UltronVoice::Event &ev) {
  ultron::feedback_play(ultron::kFeedbackEscuchando, ev.tMs);
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
  /* T14 (FR-018/FR-019/FR-037/NFR-010): tell the library how long each committed
   * clip is. It arms the playback interval on the Wake/Command edge with these
   * values — CONFIRMING lasts "Escuchando" and the 5 s window starts at its end;
   * the "comando" interval makes the windows that hear it non-deciding. The
   * durations come from the assets (clips.h), never from a literal here. */
  options.wakeClipMs = ultron::feedback_clip(ultron::kFeedbackEscuchando)->duration_ms;
  options.commandClipMs = ultron::feedback_clip(ultron::kFeedbackComando)->duration_ms;
  voice.begin(options); /* prints the boot banner; EVT ERROR + halt on any fault */

  /* T14: the speaker is part of the boot. A missing/unusable output device
   * aborts with EVT ERROR code=SPK_INIT_FAILED (g_halted stops the loop). */
  audioBegin();

  /* T15 (NFR-009): the external pull-ups held the coils OFF from reset; this is
   * the explicit OFF + the 50 ms tick, before any command can arrive. Runs even
   * when begin() failed, so a deaf device still has its outputs safe. */
  outputsBegin();
}

void loop() {
  /* T14: SPK_INIT_FAILED at boot — stay halted (outputs were left OFF by the
   * external pull-ups, so the relay side is safe too). */
  if (g_halted) {
    delay(1000);
    return;
  }

  UltronVoice::Event ev;
  const bool got = voice.poll(ev);

  /* T14: keep the confirmation clip fed. UltronVoice::poll() blocks ~100 ms on
   * the microphone read, so the player is serviced here, right after it, with a
   * bounded lead (never a whole-clip wait). */
  ultron::feedback_service(millis());

  /* T15: relay edges come from the 50 ms tick, not from this loop. */
  if (g_outputs_ready) outputsPoll();
  if (!got) return;

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
      /* Fault: outputs drop to OFF instead of holding a stale command (NFR-009). */
      if (g_outputs_ready) {
        const uint32_t now = millis();
        g_output_ticker.locked([&] { g_outputs.failSafe(now); });
        voice.console().printf("DBG out failsafe=1 t_ms=%lu\r\n", (unsigned long)now);
        outputsPoll();
      }
      break;
  }
}
