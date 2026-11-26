/*
 * ultron_voice — application sketch: recognised commands drive the relay outputs.
 * Seeed XIAO ESP32S3 Sense · spec 003-can-act (T12 firmware core + the relay logic).
 *
 * Everything voice-related lives in the **UltronVoice** library
 * (devices/esp32/libraries/UltronVoice): PDM capture, MFCC, TFLite-Micro, thresholds,
 * the WAKE_LISTEN/CONFIRMING/LISTENING machine and the Target Event Contract lines.
 * This file is only the part a human should read: what happens when a command is
 * recognised.
 *
 * Wiring (design.md → "Relay hardware"; active-LOW board, pull-ups to 3V3, coils on
 * external 5 V so the channels are OFF at boot — NFR-009):
 *
 *   D5 (GPIO6) ──► relay IN1 ──► LM  (luz media)
 *   D8 (GPIO7) ──► relay IN2 ──► LB  (luz baja)
 *   LA is not wired in this milestone — `la` drives LB+LM and says so in the log.
 *
 * Luz hierarchy (Actuation Behavior Contract): lm → LM; lb → LM+LB; la → LM+LB (+LA
 * in spec 006). A luz command replaces the previous luz state; every other trained
 * command is a no-op here until its channel exists (spec 006 rollout).
 *
 * Build/flash: ./scripts/deploy_xiao.sh [--selftest] [--listen 45]
 * Console: `EVT CMD id=lb conf=0.91 t_ms=…` then the `DBG out=… state=on` lines.
 */

#include <UltronVoice.h>
#include <Arduino.h>

namespace {

/* Relay channels — active-LOW: LOW energises the coil. */
constexpr uint8_t kRelayLm = 6; /* D5 */
constexpr uint8_t kRelayLb = 7; /* D8 */

UltronVoice voice;

/* Luz state: which channels the current luz command wants ON. */
struct LuzState {
  bool lm = false;
  bool lb = false;
};

void writeChannel(const char *id, uint8_t pin, bool on) {
  digitalWrite(pin, on ? LOW : HIGH);
  char line[64];
  snprintf(line, sizeof(line), "DBG out=%s state=%s t_ms=%lu", id, on ? "on" : "off",
           (unsigned long)millis());
  voice.console().println(line);
}

void applyLuz(const LuzState &next) {
  static LuzState current; /* controller memory; all channels are OFF at boot */
  if (next.lm != current.lm) writeChannel("lm", kRelayLm, next.lm);
  if (next.lb != current.lb) writeChannel("lb", kRelayLb, next.lb);
  current = next;
  if (next.lm && next.lb) {
    /* `la` also wants LA, which does not exist until spec 006. Say it out loud. */
    voice.console().println("DBG out=la state=unwired");
  }
}

void allOff() {
  digitalWrite(kRelayLm, HIGH);
  digitalWrite(kRelayLb, HIGH);
}

}  // namespace

void setup() {
  /* NFR-009: outputs OFF before anything else can take time. */
  pinMode(kRelayLm, OUTPUT);
  pinMode(kRelayLb, OUTPUT);
  allOff();

  Serial.begin(115200);
  const uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 2000) {
    delay(10);
  }

  UltronVoice::Options options;
#ifdef ULTRON_SELFTEST
  options.selftest = true; /* scripted scores: event contract proof, not recognition */
#endif
  /* Audio feedback is T14; when that library layer lands the app sets the clip
   * lengths here (wakeClipMs = 500, commandClipMs = 300) and plays the audio on the
   * Wake/Command events below. The library already owns the timing bookkeeping. */
  options.wakeClipMs = 0;
  options.commandClipMs = 0;

  voice.begin(options);
}

void loop() {
  UltronVoice::Event ev;
  if (!voice.poll(ev)) return;

  switch (ev.type) {
    case UltronVoice::Type::Wake:
      /* Wake accepted — the command window is open. Nothing to actuate yet: no
       * channel ever toggles without a command event (NFR-009). */
      break;

    case UltronVoice::Type::Command:
      if (ev.id == nullptr) break;
      if (strcmp(ev.id, "lm") == 0) {
        applyLuz({true, false});
      } else if (strcmp(ev.id, "lb") == 0) {
        applyLuz({true, true});
      } else if (strcmp(ev.id, "la") == 0) {
        applyLuz({true, true});
      } else {
        /* Trained later (spec 006 rollout): gd, gi, p, cp, lpb, lpa, a, cc. */
        char line[80];
        snprintf(line, sizeof(line), "DBG out=%s state=unwired t_ms=%lu", ev.id,
                 (unsigned long)millis());
        voice.console().println(line);
      }
      break;

    case UltronVoice::Type::Timeout:
      /* Command window closed with nothing recognised: luz state stays as it is. */
      break;

    case UltronVoice::Type::Error:
      /* Fail loud and safe: a fault must never leave a channel energised. */
      allOff();
      break;
  }
}
