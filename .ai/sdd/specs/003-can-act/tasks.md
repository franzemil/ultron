# Tasks — 003 Can Act (Milestone M3)

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-001, FR-002, FR-004, FR-005, FR-006, FR-007 | T10b, T11 |
| FR-003, FR-008 | T10b, T11 |
| FR-009, FR-017, NFR-009 | T10c, T11 |
| FR-018, FR-019, NFR-010 | T15 |
| NFR-001, NFR-002 | T10b, T11 |
| NFR-008 | T10b, T11 |
| US-001 | T10b, T10c, T11 |
| US-004 | T10b, T11 |
| US-007 | T15 |

Milestone M3 = phase P5 of [.ai/sdd/PLAN.md](../../PLAN.md). Depends on spec 001 (MFCC library)
and spec 002 (model artifacts + T10a spike outcome).

## Implementation readiness check

- [ ] `.status` reads `requirements:approved` (currently `requirements:draft`)
- [ ] `.status` reads `design:approved`
- [ ] `.status` reads `tasks:approved` — **gates all implementation work**
- [ ] **Q-011 (wiper stop behavior) resolved — the only open question in the system**
- [ ] Specs 001/002 delivered: MFCC library, `model_int8.tflite` / `model_fp16.tflite`,
      `labels.json`, thresholds
- [ ] Hardware present: relays, MAX98357A + speaker (XIAO), USB audio dongle + speaker (Pi)

Implementation must be executed in dependency order; each task is verified by its own acceptance
criteria before the next begins.

## Tasks

### T10b — Firmware core (3 h)
- Covers: FR-001…FR-008, NFR-001, NFR-002, NFR-008. Deps: spec 001 T6, spec 002 T9 + T10a.
  Priority: Must.
- Checklist: PDM capture (2 s ring, 500 ms hop), C MFCC build, TFLM interpreter + AllOpsResolver,
  state machine (design table), event contract serial lines, `DBG pred=` lines, static buffers,
  `devices/esp32/ultron_voice/config.h` (thresholds + registry injected at build).
- Likely files: `devices/esp32/ultron_voice/ultron_voice.ino`,
  `devices/esp32/ultron_voice/dsp/*`, `devices/esp32/ultron_voice/config.h`.
- Verify: US-001 AC1–AC3 executed live against the device (event log captured); single-window
  inference ≤ 400 ms logged; serial emits contract lines only.

### T10c — Firmware output controller + relays (3 h)
- Covers: FR-009, FR-017, NFR-009. Deps: T10b, spec 001 T3 (registry). Priority: Must.
- Checklist: 9-channel GPIO layer, behavior engines (`steady` / `blink_45s` /
  `blink_until_cancel` / `cancel` / `motor` / `pulse_2s` / `pulse_05s`), luz hierarchy, GD/GI-vs-P
  priority rules, 50 ms non-blocking tick, boot-safe outputs, `DBG out=` lines, pin allocation
  documented (PCF8574 fallback per TD-009).
- Likely files: `devices/esp32/ultron_voice/outputs.{h,cpp}`, `devices/esp32/ultron_voice/pins.h`.
- Verify: scripted commands produce contract-conformant channel timing (blink 1 s/1 s × 45 s,
  A = 2 s ±50 ms, CC = 0.5 s ±50 ms, CP stops parqueo < 200 ms); all channels OFF at boot.

### T11 — Orange Pi runtime + outputs (5 h)
- Covers: FR-001…FR-009, FR-015 (per-class thresholds applied), FR-017, NFR-001, NFR-008,
  NFR-009. Deps: spec 001 T6, spec 002 T9. Priority: Must.
- Checklist: `devices/orangepi/ultron_pi.py` — sounddevice capture (16 k/16-bit), ctypes MFCC
  binding, tflite_runtime interpreter (float16/int8), same state machine semantics, JSON event
  lines to stdout, fail-fast mic listing (Q-003 rule, spec 001); output controller via libgpiod
  driving the 3.3 V relay board — same behavior engines as T10c (divergence between targets is a
  bug); systemd user-service snippet in docs.
- Likely files: `devices/orangepi/ultron_pi.py`, `devices/orangepi/outputs.py`,
  `devices/orangepi/Makefile` (builds `dsp` .so), `docs/pi.md`.
- Verify: same US-001 AC1–AC3 script passes on the Pi; inference ≤ 200 ms logged; event JSON
  validates against contract.

### T15 — Wake feedback: "Escuchando" on both targets (3 h)
- Covers: FR-018, FR-019, NFR-010, US-007. Deps: T10b (XIAO core), T11 (Pi runtime). Priority:
  Must.
- Checklist: render + commit `assets/escuchando.wav` (Spanish TTS, 16 k/16-bit/mono, ≤ 500 ms,
  silence-trimmed); XIAO I2S output to MAX98357A + speaker with the `CONFIRMING` state and
  playback-overlap window discard (design, TD-011); Pi playback via `sounddevice` through the USB
  dongle; identical trigger/timing/fail-fast (`SPK_INIT_FAILED`) semantics on both targets;
  `DBG play=` lines; wiring + volume notes.
- Likely files: `assets/escuchando.wav`, `devices/esp32/ultron_voice/feedback.{h,cpp}`,
  `devices/orangepi/feedback.py`, `docs/feedback.md`.
- Verify: on both devices — "hey ultron" → `WAKE` event → clip audible ≤ 200 ms later and ends
  ≤ 500 ms after start; a command spoken right after the clip is accepted (window started at
  clip end); below-threshold audio never plays anything; missing output device fails boot with
  `SPK_INIT_FAILED`.

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).
