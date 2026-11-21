// ============================================================================
// relay_probe — hardware-validation SPIKE for the Seeed XIAO ESP32S3 Sense
// ============================================================================
// (a) This is a HARDWARE-VALIDATION SPIKE. It exercises the two temporary
//     scaffold relay channels only and marks NO spec 003 task complete.
// (b) Pin assignments + active-LOW polarity (spec 003 design.md "Output
//     controller" / "Wiring (XIAO stack)"):
//       IN1 -> D5 (GPIO6)  = channel 1 ("lm", steady)
//       IN2 -> D6 (GPIO43) = channel 2 ("gd", blink_45s)
//     The relay board is ACTIVE-LOW with pull-ups to 3V3, so a floating or
//     HIGH input keeps a channel OFF; driving the pin LOW turns it ON.
//     Relay coils are on an EXTERNAL 5 V supply (never the XIAO), JD-VCC
//     jumper removed, common GND bus — power wiring is never touched in code.
// (c) Temporary T15 scaffolding (FR-038 Stage-B bring-up, requirements.md):
//     GPIO43 is also the default UART0-TX pad, so this probe occupies it only
//     until it is removed at the end of task T15. USB-C (native USB, GPIO19/20)
//     is the console, independent of this pin.
// (d) References: spec 003 design.md "Output controller" + "Wiring (XIAO
//     stack)"; requirements FR-038 (companion SuperMini drives 9 relays over
//     UART) and NFR-009 (all channels OFF at boot/reset, no channel toggles
//     without a command event). Behavior engines mirror the Actuation Behavior
//     Contract (steady / blink_45s / pulse_2s / pulse_05s).
// (e) The SHIPPING topology is the companion ESP32-S3 SuperMini driving all 9
//     relay channels over UART (D8/D9 on the XIAO); this sketch only drives the
//     2 temporary bring-up channels. Sibling validated spike:
//     devices/esp32/spike_audio/.
// ============================================================================

#include <Arduino.h>
#include <string.h>
#include <stdio.h>

// --- Pin map (XIAO ESP32S3 Sense) ---
static constexpr uint8_t PIN_LM = 6;    // D5  (GPIO6)  = IN1, channel 1 "lm"
static constexpr uint8_t PIN_GD = 43;   // D6  (GPIO43) = IN2, channel 2 "gd"

// Relay board is ACTIVE-LOW: LOW = channel ON, HIGH = channel OFF.
static const uint8_t PIN_CH[2] = {PIN_LM, PIN_GD};
static const char* const CH_ID[2] = {"lm", "gd"};

// --- Behavior-engine modes (static only; loop() task stack is ~8 KB) ---
enum : uint8_t {
  MODE_OFF = 0,
  MODE_STEADY = 1,   // latched ON until replaced
  MODE_BLINK = 2,    // 1 s ON / 1 s OFF for a total duration, then OFF
  MODE_PULSE = 3,    // single timed ON pulse, then OFF
};

static const char* const MODE_NAME[4] = {"off", "steady", "blink", "pulse"};

static bool     chRelayOn[2] = {false, false};  // relay channel state (true = ON)
static uint8_t  chMode[2]    = {MODE_OFF, MODE_OFF};
static uint32_t chElapsed[2] = {0, 0};          // ms since mode start
static uint32_t chTotal[2]   = {0, 0};          // total ms (blink/pulse)
static uint32_t chPhase[2]   = {0, 0};          // ms in current on/off phase (blink)

static constexpr uint32_t TICK_MS        = 50;    // non-blocking tick (behavior engines)
static constexpr uint32_t BLINK_PHASE_MS = 1000;  // 1 s ON / 1 s OFF

static uint32_t lastTickMs = 0;

// --- Serial line + error buffers (static, no heap) ---
static constexpr uint8_t LINE_MAX = 48;
static char line[LINE_MAX + 1];
static char rawCmd[LINE_MAX + 1];
static char lastErr[96];
static uint8_t lineLen = 0;

// ---------------------------------------------------------------------------
// Apply the relay state for one channel; log on every physical change.
// Log line MUST match the observability contract:
//   DBG out=<id> state=on|off t_ms=<ms>
// ---------------------------------------------------------------------------
static void setRelay(uint8_t idx, bool on) {
  if (chRelayOn[idx] == on) {
    return;
  }
  chRelayOn[idx] = on;
  digitalWrite(PIN_CH[idx], on ? LOW : HIGH);  // active-LOW: ON => drive LOW
  Serial.printf("DBG out=%s state=%s t_ms=%lu\n",
                CH_ID[idx], on ? "on" : "off", (unsigned long)millis());
}

// Force every channel OFF (NFR-009; used for malformed commands).
static void allOff() {
  for (uint8_t i = 0; i < 2; i++) {
    chMode[i] = MODE_OFF;
    setRelay(i, false);
  }
}

// Record + echo an error (lastErr is surfaced by `relay status`).
static void setError(const char* msg) {
  snprintf(lastErr, sizeof(lastErr), "%s", msg);
  Serial.printf("ERR: %s\n", msg);
}

// Strict unsigned-integer parse: the whole token must be digits.
static bool parseUInt(const char* s, uint32_t* out) {
  if (s == nullptr || *s == '\0') {
    return false;
  }
  uint32_t v = 0;
  for (const char* p = s; *p; ++p) {
    if (*p < '0' || *p > '9') {
      return false;
    }
    uint32_t d = (uint32_t)(*p - '0');
    if (v > (0xFFFFFFFFUL - d) / 10) {
      return false;  // overflow
    }
    v = v * 10 + d;
  }
  *out = v;
  return true;
}

// --- Command handlers (1-based channel -> 0-based index) ---
static void cmdOn(uint8_t idx) {
  chMode[idx] = MODE_STEADY;
  setRelay(idx, true);
}

static void cmdOff(uint8_t idx) {
  chMode[idx] = MODE_OFF;
  setRelay(idx, false);
}

static void cmdBlink(uint8_t idx, uint32_t ms) {
  chMode[idx] = MODE_BLINK;
  chElapsed[idx] = 0;
  chPhase[idx] = 0;
  chTotal[idx] = ms;
  setRelay(idx, true);  // start in the 1 s ON phase
}

static void cmdPulse(uint8_t idx, uint32_t ms) {
  chMode[idx] = MODE_PULSE;
  chElapsed[idx] = 0;
  chTotal[idx] = ms;
  setRelay(idx, true);
}

// Human-readable, EVT-free echo of each accepted command.
static void echoOk(const char* op, uint8_t ch, uint32_t ms, bool hasMs) {
  if (hasMs) {
    Serial.printf("ok: relay %s %u %lu -> %s\n",
                  op, (unsigned)ch, (unsigned long)ms, CH_ID[ch - 1]);
  } else {
    Serial.printf("ok: relay %s %u -> %s\n",
                  op, (unsigned)ch, CH_ID[ch - 1]);
  }
}

static void printStatus() {
  Serial.printf("status: lm=%s(%s) gd=%s(%s)\n",
                chRelayOn[0] ? "on" : "off", MODE_NAME[chMode[0]],
                chRelayOn[1] ? "on" : "off", MODE_NAME[chMode[1]]);
  Serial.printf("last_error: %s\n", lastErr);
}

// ---------------------------------------------------------------------------
// Parse one newline-terminated command.
//   relay on <ch> | off <ch> | blink <ch> <ms> | pulse <ch> <ms> | status
// Safety contract:
//   - unknown command (not "relay ...", or unknown op)  -> error, no change
//   - out-of-range channel                              -> error, no change
//   - malformed relay command (bad/missing/extra args)  -> error + all OFF
// ---------------------------------------------------------------------------
static void handleCommand(char* cmd) {
  snprintf(rawCmd, sizeof(rawCmd), "%s", cmd);  // keep a pristine copy for errors

  char* save = nullptr;
  char* tok = strtok_r(cmd, " ", &save);

  if (tok == nullptr || strcmp(tok, "relay") != 0) {
    char buf[96];
    snprintf(buf, sizeof(buf), "unknown command: %.48s", rawCmd);
    setError(buf);
    return;
  }

  tok = strtok_r(nullptr, " ", &save);  // op
  if (tok == nullptr) {
    setError("malformed command");
    allOff();
    return;
  }

  if (strcmp(tok, "status") == 0) {
    tok = strtok_r(nullptr, " ", &save);
    if (tok != nullptr) {
      setError("malformed command");
      allOff();
      return;
    }
    printStatus();
    return;
  }

  const char* op = tok;
  bool isOn  = strcmp(tok, "on") == 0;
  bool isOff = strcmp(tok, "off") == 0;
  bool isBlk = strcmp(tok, "blink") == 0;
  bool isPls = strcmp(tok, "pulse") == 0;
  if (!(isOn || isOff || isBlk || isPls)) {
    char buf[96];
    snprintf(buf, sizeof(buf), "unknown command: %.48s", rawCmd);
    setError(buf);
    return;
  }

  tok = strtok_r(nullptr, " ", &save);  // <ch>
  if (tok == nullptr) {
    setError("malformed command");
    allOff();
    return;
  }
  uint32_t chVal = 0;
  if (!parseUInt(tok, &chVal) || chVal < 1 || chVal > 2) {
    char buf[96];
    snprintf(buf, sizeof(buf), "channel %s out of range (1..2)", tok);
    setError(buf);  // error only — change nothing
    return;
  }
  uint8_t idx = (uint8_t)(chVal - 1);

  if (isOn || isOff) {
    tok = strtok_r(nullptr, " ", &save);
    if (tok != nullptr) {  // extra argument
      setError("malformed command");
      allOff();
      return;
    }
    if (isOn) {
      cmdOn(idx);
      echoOk("on", chVal, 0, false);
    } else {
      cmdOff(idx);  // also cancels any running blink/pulse
      echoOk("off", chVal, 0, false);
    }
    return;
  }

  // blink / pulse: require <ms>
  tok = strtok_r(nullptr, " ", &save);
  if (tok == nullptr) {
    setError("malformed command");
    allOff();
    return;
  }
  uint32_t ms = 0;
  if (!parseUInt(tok, &ms) || ms == 0) {
    setError("malformed command (ms must be a positive integer)");
    allOff();
    return;
  }
  tok = strtok_r(nullptr, " ", &save);
  if (tok != nullptr) {  // extra argument
    setError("malformed command");
    allOff();
    return;
  }

  if (isBlk) {
    cmdBlink(idx, ms);
    echoOk("blink", chVal, ms, true);
  } else {
    cmdPulse(idx, ms);
    echoOk("pulse", chVal, ms, true);
  }
}

// --- Read available bytes, accumulate a line, dispatch on newline ---
static void readSerial() {
  while (Serial.available() > 0) {
    int c = Serial.read();
    if (c < 0) {
      break;
    }
    if (c == '\n' || c == '\r') {
      if (lineLen > 0) {
        line[lineLen] = '\0';
        handleCommand(line);
        lineLen = 0;
      }
    } else if (lineLen < LINE_MAX) {
      line[lineLen++] = (char)c;
    } else {
      // Over-long line: discard and fail safe.
      lineLen = 0;
      setError("malformed command (line too long)");
      allOff();
    }
  }
}

// --- Non-blocking 50 ms behavior-engine tick ---
static void tickEngines() {
  for (uint8_t i = 0; i < 2; i++) {
    switch (chMode[i]) {
      case MODE_OFF:
      case MODE_STEADY:
        break;  // latched GPIO level already reflects steady state

      case MODE_BLINK: {
        chElapsed[i] += TICK_MS;
        if (chElapsed[i] >= chTotal[i]) {
          chMode[i] = MODE_OFF;      // total elapsed -> OFF (NFR-009)
          setRelay(i, false);
          break;
        }
        chPhase[i] += TICK_MS;
        if (chPhase[i] >= BLINK_PHASE_MS) {
          chPhase[i] -= BLINK_PHASE_MS;
          setRelay(i, !chRelayOn[i]);  // 1 s ON <-> 1 s OFF
        }
        break;
      }

      case MODE_PULSE: {
        chElapsed[i] += TICK_MS;
        if (chElapsed[i] >= chTotal[i]) {
          chMode[i] = MODE_OFF;      // pulse done -> OFF
          setRelay(i, false);
        }
        break;
      }
    }
  }
}

void setup() {
  // Boot safety (NFR-009): force both pins HIGH (channels OFF) BEFORE enabling
  // them as outputs. This ordering is safe in the ESP32 Arduino core:
  // digitalWrite() latches the value into the GPIO matrix, then pinMode()
  // enables the output driver — the pin never glitches LOW (never turns a
  // channel ON) at boot.
  digitalWrite(PIN_LM, HIGH);
  digitalWrite(PIN_GD, HIGH);
  pinMode(PIN_LM, OUTPUT);
  pinMode(PIN_GD, OUTPUT);

  Serial.begin(115200);
  delay(50);  // initial boot settle (the only blocking delay in the sketch)

  uint32_t t0 = millis();
  while (!Serial && (millis() - t0) < 2000) {
    // wait briefly for the USB-C console to attach
  }

  Serial.println();
  Serial.println("relay_probe — hardware-validation SPIKE (marks nothing complete)");
  Serial.println("board: Seeed XIAO ESP32S3 Sense");
  Serial.println("ch1 (lm) IN1 -> D5  (GPIO6)");
  Serial.println("ch2 (gd) IN2 -> D6  (GPIO43, U0TXD pad — temporary T15 scaffold)");
  Serial.println("relay board ACTIVE-LOW: pin LOW = channel ON, HIGH = OFF");
  Serial.println("coils on external 5 V; JD-VCC jumper removed; common GND bus");
  Serial.println("temporary T15 scaffolding — removed at end of T15; no task complete");
  Serial.println("commands: relay on|off|blink|pulse <1|2> [ms] | relay status");
  Serial.println();

  snprintf(lastErr, sizeof(lastErr), "(none)");
  lastTickMs = millis();
}

void loop() {
  readSerial();

  uint32_t now = millis();
  if (now - lastTickMs >= TICK_MS) {
    lastTickMs += TICK_MS;
    tickEngines();
  }
}
