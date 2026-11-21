# Requirements — 003 Can Act (Milestone 3)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context — read it first), [002-can-think](../002-can-think/) (model artifacts),
> [004-can-decide](../004-can-decide/) (bench), [005-archived](../005-archived/) (EI sync).
> FR/NFR IDs are **global** across all milestone specs.

## Overview

Milestone 3 puts the loop on the XIAO ESP32S3: firmware (PDM capture, MFCC, TFLite-Micro,
state machine, audio feedback) implementing the two-stage interaction — wake on **"hey ultron"** →
"Escuchando" playback → 5 s command window → event + short confirmation clip → timeout — with
**2 relay channels driven directly from the XIAO** (LM + LB) for the minimal trained command set
(`la`, `lb`). Every accepted wake speaks ("Escuchando") and every executed command plays a short
fixed confirmation clip. **Scope decisions (owner, 2026-11-21):** the Orange Pi runtime and the
cross-target comparison live in the **final phase (spec 005)**; the 9-channel companion ESP32-S3
SuperMini stack, the remaining relay channels, and the per-command rollout live in
**spec 006-can-act-full**. This milestone is staged: **Stage A** deploys the minimal 4-class
model (`wake`, `noise`, `la`, `lb` — spec 002 TD-012) console-only; **Stage B** adds audio
feedback and the 2 direct relays. This milestone also covers the XIAO side of the device-mic
recording pass (US-002, moved from spec 001 — owner decision, 2026-11-19).

## Goals

1. The XIAO passes the US-001 interaction script end-to-end on the currently trained command set
   (`la`, `lb`) — events from Stage A, event + actuation from Stage B on.
2. Wake acceptance is confirmed audibly with "Escuchando" and every executed command with a short
   confirmation clip.
3. Timing tolerances and boot-safe outputs on the XIAO; the Target Event Contract stays
   target-agnostic so the Pi (spec 005) and the companion stack (spec 006) implement it unchanged.
4. The XIAO side of the device-mic recording pass (US-002, moved from spec 001) produces valid
   training clips.

## Non-Goals

- No dataset/training work here (specs 001–002) — this spec consumes their artifacts.
- No benchmarking or device decision here (spec 004).
- No WiFi/remote control; actuation is local (relay/GPIO).
- **No companion ESP32-S3 SuperMini and no 9-channel stack** (spec 006-can-act-full — owner
  decision 2026-11-21).
- **No Orange Pi runtime and no cross-target parity** (spec 005, final phase — owner decision
  2026-11-21).

## User Stories

### US-001 — Wake and command
**As a** device owner
**I want to** say "hey ultron" and then a registered command phrase
**so that** the device performs the mapped action without touching anything.

**Acceptance criteria:**
- **AC1:** Saying "hey ultron" then "enciende luz alta" (a command of the initially trained
  set) produces a `CMD` event for `la` and starts its mapped behavior (LM+LB+LA steady ON).
  Stage C re-runs this same script for each rolled-out command (T16–T24).
- **AC2:** Saying a command phrase alone (no wake) produces no command event and no action.
- **AC3:** Saying "hey ultron" then nothing for 5 s produces a `TIMEOUT` event and the device
  returns to wake-listening.

### US-002 — Collect training data on either device
**As a** device owner
**I want to** record WAV clips of each phrase (and noise) from either candidate device
**so that** the model trains on the real microphones it will run on.

**Acceptance criteria:**
- **AC1:** The XIAO recorder writes 16 kHz/16-bit/mono WAVs labeled by phrase to storage.
- **AC2:** *(Pi recorder clause deferred to spec 005, final phase — owner decision 2026-11-21.)*
- **AC3:** Every recording is DC-offset-correctable and validates (`ultron dataset validate`).

*(Amendment 2026-11-19: on-device recording pass moved from spec 001 — owner decision;
implemented here as T28 using the spec 001 T4 recorder utilities.)*

### US-007 — Audible wake confirmation
**As a** device owner
**I want to** hear the device say "Escuchando" right after "hey ultron" is accepted
**so that** I know the wake word was recognized and can speak the command immediately.

**Acceptance criteria:**
- **AC1:** An accepted "hey ultron" plays the fixed "Escuchando" clip through the speaker with a
  deterministic trigger and timing (the same asset is used unchanged when the Pi arrives in spec
  005).
- **AC2:** The 5 s command window starts when playback ends; a command spoken right after the
  clip is accepted (US-001 AC1 still passes end-to-end with playback on).
- **AC3:** No audio plays while in wake-listening — below-threshold scores stay silent (FR-006).

## Functional Requirements (EARS, MoSCoW)

**FR-001 (Must)** — WHEN a wake phrase scores ≥ wake threshold on the wake stage,
THE SYSTEM SHALL enter command-listening state within 1.5 s of the end of the utterance
(the Pi's tighter 1.0 s budget is specified with its runtime in spec 005).

**FR-002 (Must)** — WHILE in command-listening state, THE SYSTEM SHALL evaluate incoming audio
against the trained command set for up to 5 s (recognized labels = trained subset — FR-013/FR-032,
spec 002).

**FR-003 (Must)** — WHEN a command window scores ≥ its command threshold AND ≥ margin over every
other class, THE SYSTEM SHALL emit a command event carrying the command ID and confidence.

**FR-004 (Must)** — IF the 5 s window elapses without an accepted command,
THE SYSTEM SHALL emit a timeout event and return to wake-listening.

**FR-005 (Must)** — WHILE in wake-listening state, THE SYSTEM SHALL NOT emit command events
(bare commands without wake are ignored).

**FR-006 (Must)** — WHEN all wake-stage scores are below the wake threshold,
THE SYSTEM SHALL remain in wake-listening.

**FR-007 (Must)** — THE SYSTEM SHALL perform all inference on-device with no network dependency.

**FR-008 (Must)** — THE SYSTEM SHALL emit events over the Target Event Contract. The contract is
target-agnostic; this milestone implements the XIAO serial form (the Pi JSON form lands in spec
005 with its runtime).

**FR-009 (Must)** — WHEN a command event is emitted, THE SYSTEM SHALL drive its mapped relay
channel(s) exactly as defined in the Actuation Behavior Contract, within timing tolerances
(blink ±100 ms, pulses ±50 ms, CP stop < 200 ms).

**FR-017 (Must)** — WHEN a CP ("cancelar parqueo") command event is emitted AND parqueo is
active, THE SYSTEM SHALL stop the GD and GI blinking and leave both outputs OFF. IF parqueo is
not active, THE SYSTEM SHALL treat CP as a no-op.

**FR-018 (Must)** — WHEN a wake phrase is accepted, THE SYSTEM SHALL play the fixed
"Escuchando" confirmation clip through a speaker. The clip is a pre-rendered Spanish-TTS WAV
(16 kHz / 16-bit / mono, ≤ 500 ms) committed to the repo and byte-identical on both targets;
no target performs runtime speech synthesis.

**FR-019 (Must)** — THE SYSTEM SHALL keep wake feedback deterministic: the edge that emits
`WAKE` triggers playback, which starts ≤ 200 ms after acceptance, and the 5 s command window
(FR-002) starts when playback ends, with the whole chain inside FR-001's entry budget. Audio
frames captured during playback SHALL NOT contribute to classification decisions.

**FR-037 (Must)** — WHEN a command event is emitted, THE SYSTEM SHALL play the fixed
command-confirmation clip `assets/comando.wav` — a pre-rendered clip (16 kHz / 16-bit / mono,
≤ 300 ms) committed to the repo. Playback SHALL start ≤ 200 ms after acceptance; audio frames
overlapping playback SHALL NOT contribute to classification decisions (same rule as FR-019). No
listening time is lost: the command window has already closed when the clip plays.

**FR-038 (Must)** — *(Moved to spec 006-can-act-full by owner decision 2026-11-21 — the
companion ESP32-S3 SuperMini stack is out of this milestone. This milestone instead drives
≤ 2 relay channels directly from the XIAO; global ID preserved.)*

## Actuation Behavior Contract

| Sigla | Command | Channels driven | Behavior |
|---|---|---|---|
| LM | luz media | LM | Steady ON until another luz command replaces it |
| LB | luz baja | LM + LB | Steady ON both |
| LA | luz alta | LM + LB + LA | Steady ON all three |
| GD | guiñador derecho | GD | Blink 1 s on / 1 s off for 45 s, then OFF |
| GI | guiñador izquierdo | GI | Blink 1 s on / 1 s off for 45 s, then OFF |
| P | parqueo | GD + GI (in phase) | Blink 1 s on / 1 s off until CP |
| CP | cancelar parqueo | GD, GI | Stop parqueo blinking; both OFF |
| LPB | limpia parabrisas baja | LPB | Motor output ON for a fixed 45 s window, then OFF |
| LPA | limpia parabrisas alta | LPA | Motor output ON for a fixed 45 s window, then OFF |
| A | arranque | A | Pulse exactly 2 s |
| CC | cierre centralizado | CC | Pulse exactly 0.5 s |

Priority rules: starting GD or GI while parqueo is active cancels parqueo and starts the
single-side blink; starting parqueo overrides any single-side blink. A luz command replaces the
previous luz state (off / lm / lb / la). Wipers (LPB/LPA) are mutually exclusive: selecting one
cancels the other and starts its fixed 45 s window; re-selecting the same speed restarts the
window. CP affects parqueo only — never wipers. All channels are OFF at boot. Accepted voice
phrases per command: the bare action name and the "enciende" variant (e.g. *"luz media"* /
*"enciende luz media"*); CP is *"cancelar parqueo"*.

## Non-Functional Requirements

**NFR-001 (Latency)** — single 1 s-equivalent window inference: ≤ 400 ms on the XIAO ESP32S3 @
240 MHz (the Pi's ≤ 200 ms budget is specified with its runtime in spec 005). End-to-end wake
reaction per FR-001.

**NFR-002 (Memory, XIAO)** — int8 model ≤ 100 KB; TFLM arena ≤ 150 KB; firmware fits the
XIAO_ESP32S3 flash without disabling the microphone/SD features; wake path must not depend on
PSRAM being present.

**NFR-008 (Observability)** — devices log one prediction line per window (class + confidence) at
debug level; bench produces a markdown table in `docs/bench.md` (spec 004).

**NFR-009 (Actuation safety)** — all relay channels OFF at boot and after reset; arranque (A)
only ever pulses ≤ 2 s and only from a fresh wake → command sequence; no channel ever toggles
without a command event.

**NFR-010 (Feedback latency)** — "Escuchando" playback starts ≤ 200 ms after wake acceptance and
lasts ≤ 500 ms; the command-confirmation clip starts ≤ 200 ms after command acceptance and lasts
≤ 300 ms; end-of-utterance → command-listening stays within FR-001's budget (≤ 1.5 s on the
XIAO). The same budgets apply unchanged when the Pi arrives (spec 005).

## Constraints (this milestone)

- Audio output (FR-018, FR-037): XIAO — MAX98357A I2S DAC/amp + small speaker (owner-supplied).
  "Escuchando" and "comando" are repo-committed pre-rendered clips; no runtime TTS.
- Relay hardware: a **2-channel relay sub-board** driven directly from XIAO spare pins
  (active-LOW inputs, pull-ups to 3V3, coils on external 5 V). The 9-channel board and the
  companion ESP32-S3 SuperMini belong to spec 006.
- Runtime capture mic as in the shared context (XIAO PDM, on the Sense expansion board).
- Mic, speaker, and the 2-channel relay sub-board must be physically present before Milestone 3
  work starts (PLAN.md hardware logistics).
- Initial deployment model = the 4-class artifact (`wake`, `noise`, `la`, `lb` — spec 002
  TD-012); the recognized set grows only in spec 006's rollout cycles.

## Decisions

- **D-002** — Two-stage interaction (wake → command window). *(Fewer false triggers; commands
  alone must not actuate.)*
- **D-006** — Wake acceptance is confirmed audibly with a fixed pre-rendered TTS clip
  ("Escuchando") and every executed command with a second fixed clip ("comando"), shared as the
  same committed assets across every runtime; no runtime TTS anywhere. *(Owner requested spoken
  feedback; an MCU can only speak from bundled clips, and Pi-only synthesis would diverge
  behavior — AGENTS rule 5.)*

## Implementation FAQ

**Q-001: What are the exact Spanish command phrases?**
A: **Resolved.** The registry (`model/registry.json`, Actuation Behavior Contract) is
owner-defined: each action accepts the bare action name and the "enciende" variant. CP is
*"cancelar parqueo"*. Siglas are the registry IDs (`lm lb la gd gi p cp lpb lpa a cc`).

**Q-005: What happens if the user says "hey ultron" twice during the command window?**
A: The window timer refreshes once (5 s again). A second refresh is ignored until an event or
timeout returns to wake-listening.

**Q-010: What actuation hardware in v1?**
A: **Resolved, staged.** This milestone (003): **≤ 2 relay channels driven directly from the
XIAO** — LM + LB wired (the `lb` command drives both per registry; the `la` command drives LB+LM
of its three channels, LA landing in spec 006). The full **9-channel stack on the companion
ESP32-S3 SuperMini** (FR-038, TD-012) lives in spec 006-can-act-full; the Pi's libgpiod path
lives in spec 005.

**Q-011: How does a wiper output stop?**
A: **Resolved (owner decision, 2026-11-19).** Wipers run a **fixed 45 s window** — the same span
as the guiñadores — then stop by themselves. No CP involvement for wipers, no latching. LPB/LPA
are mutually exclusive: selecting the other speed cancels the running window and starts the new
one; re-selecting the same speed restarts it. Registry behavior: `motor_45s`.

**Q-012: How does the device say "Escuchando"?**
A: **Resolved.** One Spanish-TTS WAV (16 kHz/16-bit/mono, ≤ 500 ms, silence-trimmed) committed
to the repo; the XIAO streams it via I2S to a MAX98357A amp + speaker (the Pi plays the same
asset through a USB audio dongle from spec 005). Trigger = wake acceptance; the 5 s command
window starts at playback end (FR-019, NFR-010). Executed commands play the second fixed clip
`assets/comando.wav` (≤ 300 ms, FR-037). No other spoken feedback in v1. **XIAO path validated on
hardware (2026-11-20)**: `devices/esp32/spike_audio` streams a Spanish-TTS clip from flash to
the MAX98357A over I2S on D2/D3/D4 (GPIO3/4/5), GAIN/SD floating.

**Q-013: Which board is the companion ESP32?**
A: **Resolved (owner-supplied) — companion stack lands in spec 006.** **ESP32-S3 SuperMini** —
enough GPIOs for 9 relays + UART with spare pins, same arduino-cli / esp32-core toolchain as the
XIAO, USB-CDC console for `DBG out=` lines. Relay channels sit on non-strapping pins
(GPIO1…GPIO10; S3 strapping pins 0/3/45/46 are avoided). No PSRAM, no audio hardware, and no SD
card needed on the companion — it only parses `EVT` lines and drives GPIOs.

**Q-014: How is the MAX98357A powered on the XIAO stack?**
A: **Resolved (owner decision, 2026-11-21).** **5 V VBUS (USB) → VIN** — the amp's peak current
at 3 W output cannot come from an ESP32 GPIO; the `power_probe` spike (D10 at 3.3 V) confirmed
the amp powers and plays from a GPIO, but that remains a bench experiment only. GAIN/SD stay
floating (validated on hardware 2026-11-20).

## Success Metrics

- **Milestone 3 — "can act" (XIAO)**: Stage A — the XIAO emits contract events for the minimal
  model; Stage B — wake audio confirmation, the `la`/`lb` commands recognized with the "comando"
  clip, and the 2 direct relay channels actuating per the contract (FR-001…FR-009, FR-017…FR-019,
  FR-037, NFR-010); the XIAO device-mic recording pass produces valid clips (US-002, T28).

## Risks

- **Arduino TFLM library availability/staleness**: the early spike in spec 002 is the
  canary; documented fallback is vendoring `esp-tflite-micro`.
- **Self-hearing** (the speaker's own clip reaching the mic and skewing scores): windows
  overlapping playback are excluded from decisions (FR-019); the bench (spec 004) runs wake
  sequences with the speaker live.
- **False-wake chatter**: a false wake now speaks. Bounded by NFR-004 (spec 004: ≤ 1 false wake /
  30 min); the bench's false-accept run counts them with playback enabled.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-001–FR-009, FR-017–FR-019, and FR-037, the
Actuation Behavior Contract, the event contract, and D-002/D-006 in its own words. If any
acceptance criterion is ambiguous, ask before implementing.
