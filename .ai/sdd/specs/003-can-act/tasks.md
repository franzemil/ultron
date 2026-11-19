# Tasks — 003 Can Act (Milestone 3)

Tasks follow the **SMART structure** — every task states what makes it Specific, Measurable,
Achievable, Relevant, and Time-bound, on top of the mandated `Covers / Deps / Priority /
Checklist / Likely files / Verify` fields.

**Staged bring-up**: the hardware comes up layer by layer — console-only (T10b) → audio +
2 direct relays (T17) → full companion stack (T10c) → Pi parity (T11/T15) → recorders (T16).
Each stage is demonstrable on its own before the next begins.

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-001, FR-002, FR-004, FR-005, FR-006, FR-007 | T10b, T11 |
| FR-003, FR-008 | T10b, T11 |
| FR-009, FR-017, NFR-009 | T10c, T11 |
| FR-015 | T11 |
| FR-018, FR-019, FR-023, NFR-010 | T17, T15 |
| FR-024 | T10c |
| NFR-001, NFR-002 | T10b, T11 |
| NFR-008 | T10b, T11 |
| US-001 | T10b, T17, T10c, T11 |
| US-004 | T10b, T11 |
| US-007 | T17, T15 |
| US-002 | T16 |

This spec **is** Milestone 3 (can act) — see [.ai/sdd/PLAN.md](../../PLAN.md). Depends on spec
001 (MFCC library) and spec 002 (model artifacts + T10a spike outcome).

## Implementation readiness check

- [ ] `.status` reads `requirements:approved` (currently `requirements:draft`)
- [ ] `.status` reads `design:approved`
- [ ] `.status` reads `tasks:approved` — **gates all implementation work**
- [x] **Q-011 resolved (fixed-time wipers, owner decision 2026-11-19) — no open questions
      remain in any milestone spec**
- [ ] Specs 001/002 delivered: MFCC library, `model_int8.tflite` / `model_fp16.tflite`,
      `labels.json`, thresholds
- [ ] Hardware present: 9-channel relay board, ESP32-S3 SuperMini + UART jumpers, MAX98357A +
      speaker (XIAO), USB audio dongle + speaker (Pi)

Implementation must be executed in dependency order; each task is verified by its own acceptance
criteria before the next begins.

## Tasks

### T10b — Firmware core (console-only simulation mode)

- **Specific** — XIAO firmware implementing the audio → event path **with console output only**:
  PDM capture (2 s int16 ring buffer, 500 ms hop), MFCC via the shared C library (100 × 13),
  TFLite-Micro interpreter loading `model_int8.tflite` with `AllOpsResolver`, the
  WAKE_LISTEN ⇄ CONFIRMING ⇄ LISTENING state machine (design table), Target Event Contract serial
  lines (`EVT WAKE/CMD/TIMEOUT/ERROR`), `DBG pred=` lines, static buffers only (no dynamic
  allocation in the inference loop), and `config.h` with thresholds + registry injected at build
  time. **No audio output, no relays — every decision is judged from the USB console** (the board
  simulates the complete system on serial). Excludes output control (T10c) and audio feedback
  (T17).
- **Measurable** — Verify: US-001 AC1–AC3 executed live against the device (event log captured —
  wake, command, and timeout all visible as `EVT` lines); single-window inference ≤ 400 ms logged
  (NFR-001); serial emits contract lines only; arena + RAM stats within the design Memory budget
  (inference path ≤ 240 KB, NFR-002).
- **Achievable** — Deps: spec 001 T6 (MFCC library), spec 002 T9 (model artifacts) + T10a
  (TFLM spike go/no-go). The arena is already sized in the design Memory budget; lampara-aliz
  proved int8 TFLM runs on this exact board.
- **Relevant** — Covers FR-001…FR-008, NFR-001, NFR-002, NFR-008; implements the device side of
  US-001/US-004; feeds T17, T10c, T16, and the spec 004 bench.
- **Time-bound** — 3 h: 1 h bring-up + capture, 1 h MFCC + interpreter, 1 h state machine +
  events.
- Checklist: PDM capture (2 s ring, 500 ms hop), C MFCC build, TFLM interpreter + AllOpsResolver,
  state machine (design table), event contract serial lines, `DBG pred=` lines, static buffers,
  `devices/esp32/ultron_voice/config.h` (thresholds + registry injected at build).
- Likely files: `devices/esp32/ultron_voice/ultron_voice.ino`,
  `devices/esp32/ultron_voice/dsp/*`, `devices/esp32/ultron_voice/config.h`.

### T17 — Sound feedback + 2 direct relays on the XIAO

- **Specific** — MAX98357A I2S bring-up on XIAO pins D2/D3/D4 (design Wiring schema) with a
  player for both fixed clips: "Escuchando" on the `WAKE_LISTEN → CONFIRMING` edge and "comando"
  on the `LISTENING → WAKE_LISTEN` edge (FR-018, FR-023); `CONFIRMING` state + playback-overlap
  window discard (FR-019); `SPK_INIT_FAILED` fail-fast; `DBG play=` lines. **Plus 2 relays wired
  directly to XIAO spare pins as temporary bring-up scaffolding (FR-024 parenthetical): LM**
  (exercises the `steady` engine) **and GD** (exercises `blink_45s`) — removed by T10c.
- **Measurable** — Verify: "hey ultron" → `WAKE` → Escuchando audible ≤ 200 ms later, ends
  ≤ 500 ms after start (NFR-010); accepted command → `CMD` → comando audible ≤ 200 ms later,
  ≤ 300 ms (FR-023); LM toggles per the luz state machine; GD blinks 1 s on / 1 s off × 45 s;
  below-threshold audio never plays anything; command spoken right after the clip is accepted;
  missing output device fails boot with `SPK_INIT_FAILED`.
- **Achievable** — Deps: T10b (event source). Clips are one-time offline renders committed to the
  repo (D-006: no runtime TTS). MAX98357A + speaker + a 2-relay sub-board on the bench; I2S pins
  are free per the design pin budget.
- **Relevant** — Covers FR-018, FR-023, FR-019 (XIAO half), NFR-010, US-007; delivers the first
  audible + physical actuation proof ahead of the companion migration.
- **Time-bound** — 4 h: 1.5 h I2S bring-up + player, 1 h clip rendering + commit, 1 h 2-relay
  scaffolding, 0.5 h debug lines + docs.
- Checklist: I2S wiring (design Wiring schema), render + commit `assets/escuchando.wav` and
  `assets/comando.wav`, player with `CONFIRMING` + overlap discard, 2 direct relays (LM + GD) on
  XIAO pins, `DBG play=` + `DBG out=` lines, `SPK_INIT_FAILED` fail-fast.
- Likely files: `assets/escuchando.wav`, `assets/comando.wav`,
  `devices/esp32/ultron_voice/feedback.{h,cpp}`,
  `devices/esp32/ultron_voice/outputs_scaffold.{h,cpp}` (temporary — deleted by T10c).

### T10c — Dual-ESP link: all 9 relays migrate to the SuperMini

- **Specific** — wire the XIAO ↔ ESP32-S3 SuperMini UART per the design Wiring schema; SuperMini
  firmware parses the same `EVT` lines on UART (115200-8N1) and runs **all** behavior engines
  (`steady` / `blink_45s` / `blink_until_cancel` / `cancel` / `motor_45s` / `pulse_2s` /
  `pulse_05s`) over **9 relays on GPIO1…GPIO10** (non-strapping pins, pull-ups for active-LOW
  inputs), 50 ms non-blocking tick, boot-safe outputs (NFR-009), `DBG out=` lines on its own USB
  serial; XIAO forwards `WAKE`/`CMD` events over UART and logs `DBG uart=idle` when the link is
  absent (FR-024); **decommission the T17 scaffolding relays** (scaffold files deleted).
- **Measurable** — Verify: scripted commands over the UART link produce contract-conformant
  channel timing — blink 1 s on / 1 s off × 45 s, A = 2 s ±50 ms, CC = 0.5 s ±50 ms, CP stops
  parqueo < 200 ms, wiper window = 45 s — all channels OFF at boot, and the XIAO's voice +
  feedback behavior is unchanged with the companion attached or absent.
- **Achievable** — Deps: T10b, T17, spec 001 T3 (`registry.json` drives channels + behaviors).
  SuperMini + relay board are on the bench (readiness check); the pin map and power rules are in
  the design Wiring schema; the same arduino-cli toolchain flashes both boards.
- **Relevant** — Covers FR-009, FR-017, FR-024, NFR-009; establishes the shipping XIAO-stack
  topology (TD-012); engines stay semantics-identical to T11 (AGENTS rule 5).
- **Time-bound** — 4 h: 1 h wiring + link bring-up, 2 h SuperMini firmware (parser + engines +
  relays), 1 h scaffolding removal + end-to-end timing verify.
- Checklist: UART wiring (design schema), SuperMini firmware (EVT parser + behavior engines +
  9 relays + boot-safe), XIAO UART forwarder + `DBG uart=idle`, scaffolding removal, pin
  allocation documented (strapping pins avoided, pull-ups noted).
- Likely files: `devices/esp32/supermini_controller/supermini_controller.ino`,
  `devices/esp32/ultron_voice/uart_forward.{h,cpp}`, `docs/wiring.md`.

### T11 — Orange Pi runtime + outputs

- **Specific** — `devices/orangepi/ultron_pi.py`: sounddevice capture (16 k/16-bit), ctypes
  MFCC binding, tflite_runtime interpreter (float16/int8), the same state machine semantics as
  T10b, JSON event lines to stdout, fail-fast mic listing (spec 001 Q-003 rule), plus the output
  controller via libgpiod driving the 3.3 V relay board — the same behavior engines as T10c
  (divergence between targets is a bug). Includes a systemd user-service snippet in docs.
- **Measurable** — Verify: the same US-001 AC1–AC3 script passes on the Pi; inference ≤ 200 ms
  logged (NFR-001); event JSON validates against the contract; output-timing parity with T10c's
  scripted-command checks.
- **Achievable** — Deps: spec 001 T6 (MFCC library), spec 002 T9 (model artifacts). Debian 12
  ships libgpiod; `tflite_runtime` has an aarch64 wheel (full TensorFlow is the documented
  fallback, TD-005).
- **Relevant** — Covers FR-001…FR-009, FR-015 (per-class thresholds applied), FR-017, NFR-001,
  NFR-008, NFR-009; delivers the Pi half of US-001/US-004.
- **Time-bound** — 5 h: 1.5 h capture + DSP, 1.5 h inference + state machine, 1 h outputs,
  1 h events + docs.
- Checklist: `devices/orangepi/ultron_pi.py` — sounddevice capture (16 k/16-bit), ctypes MFCC
  binding, tflite_runtime interpreter (float16/int8), same state machine semantics, JSON event
  lines to stdout, fail-fast mic listing (Q-003 rule, spec 001); output controller via libgpiod
  driving the 3.3 V relay board — same behavior engines as T10c; systemd user-service snippet
  in docs.
- Likely files: `devices/orangepi/ultron_pi.py`, `devices/orangepi/outputs.py`,
  `devices/orangepi/Makefile` (builds `dsp` .so), `docs/pi.md`.

### T15 — Audio + actuation parity across targets

- **Specific** — Pi playback of both fixed clips ("Escuchando" + "comando") via `sounddevice`
  through the USB dongle, then a cross-target parity pass: feedback timing/semantics vs T17
  (XIAO) and actuation timing vs T10c (SuperMini) verified from recorded logs; `DBG play=` lines
  on the Pi; parity table written to `docs/feedback.md`.
- **Measurable** — Verify: on the Pi — WAKE → Escuchando ≤ 200 ms / ≤ 500 ms; CMD → comando
  ≤ 200 ms / ≤ 300 ms; below-threshold silence; missing dongle fails with `SPK_INIT_FAILED`;
  parity table shows XIAO vs Pi within the FR-009/NFR-010 tolerances.
- **Achievable** — Deps: T17 (XIAO reference), T11 (Pi runtime); USB dongle + speaker present;
  the clips are shared repo assets — no re-rendering.
- **Relevant** — Covers FR-018, FR-019, FR-023, NFR-010, US-007 (Pi side); enforces
  AGENTS rule 5 (divergence between targets is a bug) ahead of the spec 004 bench.
- **Time-bound** — 3 h: 1 h Pi playback, 1 h parity harness, 1 h docs.
- Checklist: Pi player (both clips) + fail-fast, cross-target parity harness (event + output
  logs), `docs/feedback.md` parity table, `DBG play=` lines on Pi.
- Likely files: `devices/orangepi/feedback.py`, `docs/feedback.md`.

### T16 — WAV recorders for both devices

- **Specific** — port the `wav_recorder` sketch from lampara-aliz (PSRAM=opi build, serial
  `rec {label}` command, 5 s clips to microSD) and add the Pi recorder script (`record.py`,
  sounddevice, 16 k/16-bit/mono, label argument); document the recording workflow
  (`docs/recording.md`).
- **Measurable** — Verify: XIAO records a 5 s WAV to SD (DONE response); Pi recorder writes a
  valid WAV (`ultron dataset validate` passes it).
- **Achievable** — Deps: T10b (XIAO core running), T11 (Pi runtime), spec 001 `dataset
  validate`; microSD card (FAT32) and the Pi USB mic on hand. Deferred here from spec 001 by
  owner decision (2026-11-19) — device recording starts once the hardware is live.
- **Relevant** — Covers US-002; supplies NFR-005 supplementary device-mic recordings for a
  later retrain (closes the mic-domain-shift risk noted in spec 001).
- **Time-bound** — 4 h: 2 h XIAO sketch, 1 h Pi script, 1 h docs + validation.
- Checklist: port `wav_recorder` sketch from lampara-aliz (PSRAM=opi, serial `rec {label}`);
  Pi recorder script (`devices/orangepi/record.py`, sounddevice, 16 k/16-bit/mono, label arg);
  docs.
- Likely files: `devices/esp32/wav_recorder/wav_recorder.ino`, `devices/orangepi/record.py`,
  `docs/recording.md`.

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).
