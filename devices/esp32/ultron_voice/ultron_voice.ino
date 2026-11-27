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
 *                 single source of truth for FR-009 / FR-017 / NFR-009; onCommand() below
 *                 only binds them to the console through relay_gpio.h.
 *   T14 (#41)   : audio feedback — set Options.wakeClipMs / commandClipMs and play the clip
 *                 on the Wake/Command events (the library already owns the overlap timing).
 *
 * Implemented here: T12 (library bring-up) and T15 (the relay binding below).
 *
 * Build/flash: uv run ultron esp32 deploy [--selftest] [--listen 45]
 * Expected console: boot banner, `DBG out_pins` / `DBG out_registry` / `DBG out=… state=off`,
 * then `DBG pred=…` every 500 ms and `EVT …` lines when a gate is crossed. With the
 * placeholder model no `EVT` line can appear (see PR #38).
 */

#include <Arduino.h>

#include <UltronVoice.h>

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

/* T15 seam — relay layer (FR-009 / FR-017 / NFR-009). */
void onCommand(const UltronVoice::Event &ev) {
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

  /* T15 (NFR-009): the external pull-ups held the coils OFF from reset; this is
   * the explicit OFF + the 50 ms tick, before any command can arrive. Runs even
   * when begin() failed, so a deaf device still has its outputs safe. */
  outputsBegin();
}

void loop() {
  UltronVoice::Event ev;
  const bool got = voice.poll(ev);

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
