# Requirements — 003 Can Act (Milestone 3)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context — read it first), [002-can-think](../002-can-think/) (model artifacts),
> [004-can-decide](../004-can-decide/) (bench), [005-archived](../005-archived/) (EI sync).
> FR/NFR IDs are **global** across all milestone specs.

## Overview

Milestone 3 puts the loop on hardware: the XIAO ESP32S3 firmware (PDM capture, MFCC, TFLite-Micro, state
machine, relay outputs) and the Orange Pi Zero 2W Python runtime implement the identical
two-stage interaction — wake on **"hey ultron"** → "Escuchando" playback → 5 s command window →
event or timeout — driving 9 relay channels (LM LB LA GD GI LPB LPA A CC) per the Actuation
Behavior Contract, and confirm wake acceptance by speaking. Both targets run the same model
flatbuffer and emit identical event semantics; any divergence is a bug (AGENTS rule 5).

## Goals

1. Both devices pass the US-001 interaction script end-to-end (wake → command → actuation).
2. Wake acceptance is confirmed audibly with "Escuchando", identically on both targets.
3. Identical events, timing tolerances, and boot-safe outputs on XIAO and Pi.

## Non-Goals

- No dataset/training work here (specs 001–002) — this spec consumes their artifacts.
- No benchmarking or device decision here (spec 004).
- No WiFi/remote control; actuation is local (relay/GPIO).

## User Stories

### US-001 — Wake and command
**As a** device owner
**I want to** say "hey ultron" and then a registered command phrase
**so that** the device performs the mapped action without touching anything.

**Acceptance criteria:**
- **AC1:** Saying "hey ultron" then "enciende guiñador derecho" produces a `CMD` event for `gd`
  and starts its mapped behavior (GD blinking 1 s on / 1 s off for 45 s).
- **AC2:** Saying a command phrase alone (no wake) produces no command event and no action.
- **AC3:** Saying "hey ultron" then nothing for 5 s produces a `TIMEOUT` event and the device
  returns to wake-listening.

### US-004 — Deploy the identical model to both devices
**As a** device owner
**I want to** deploy the same exported model to XIAO and Pi
**so that** the benchmark compares devices, not models.

**Acceptance criteria:**
- **AC1:** Both devices load the same `.tflite` (int8 on XIAO; int8/float16 on Pi).
- **AC2:** Both devices emit identical event types/semantics for the same audio.

### US-007 — Audible wake confirmation
**As a** device owner
**I want to** hear the device say "Escuchando" right after "hey ultron" is accepted
**so that** I know the wake word was recognized and can speak the command immediately.

**Acceptance criteria:**
- **AC1:** An accepted "hey ultron" plays the fixed "Escuchando" clip through the speaker,
  identically on XIAO and Pi (same asset, same trigger, same timing).
- **AC2:** The 5 s command window starts when playback ends; a command spoken right after the
  clip is accepted (US-001 AC1 still passes end-to-end with playback on).
- **AC3:** No audio plays while in wake-listening — below-threshold scores stay silent (FR-006).

## Functional Requirements (EARS, MoSCoW)

**FR-001 (Must)** — WHEN a wake phrase scores ≥ wake threshold on the wake stage,
THE SYSTEM SHALL enter command-listening state within 1.5 s (XIAO) / 1.0 s (Pi) of the end of the
utterance.

**FR-002 (Must)** — WHILE in command-listening state, THE SYSTEM SHALL evaluate incoming audio
against the command registry for up to 5 s.

**FR-003 (Must)** — WHEN a command window scores ≥ its command threshold AND ≥ margin over every
other class, THE SYSTEM SHALL emit a command event carrying the command ID and confidence.

**FR-004 (Must)** — IF the 5 s window elapses without an accepted command,
THE SYSTEM SHALL emit a timeout event and return to wake-listening.

**FR-005 (Must)** — WHILE in wake-listening state, THE SYSTEM SHALL NOT emit command events
(bare commands without wake are ignored).

**FR-006 (Must)** — WHEN all wake-stage scores are below the wake threshold,
THE SYSTEM SHALL remain in wake-listening.

**FR-007 (Must)** — THE SYSTEM SHALL perform all inference on-device with no network dependency.

**FR-008 (Must)** — THE SYSTEM SHALL emit events over the Target Event Contract (identical event
types and semantics on both targets — see [design](design.md#event-contract)).

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

**FR-019 (Must)** — THE SYSTEM SHALL keep wake feedback identical on both targets: same asset,
same trigger (wake acceptance — the edge that emits `WAKE`), same timing — playback starts
≤ 200 ms after acceptance and the 5 s command window (FR-002) starts when playback ends, with
the whole chain inside FR-001's entry budget. Audio frames captured during playback SHALL NOT
contribute to classification decisions.

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
| LPB | limpia parabrisas baja | LPB | Motor output ON (latched; see Q-011) |
| LPA | limpia parabrisas alta | LPA | Motor output ON (latched; see Q-011) |
| A | arranque | A | Pulse exactly 2 s |
| CC | cierre centralizado | CC | Pulse exactly 0.5 s |

Priority rules: starting GD or GI while parqueo is active cancels parqueo and starts the
single-side blink; starting parqueo overrides any single-side blink. A luz command replaces the
previous luz state (off / lm / lb / la). All channels are OFF at boot. Accepted voice phrases per
command: the bare action name and the "enciende" variant (e.g. *"luz media"* /
*"enciende luz media"*); CP is *"cancelar parqueo"*.

## Non-Functional Requirements

**NFR-001 (Latency)** — single 1 s-equivalent window inference: ≤ 400 ms on XIAO ESP32S3 @
240 MHz, ≤ 200 ms on Orange Pi Zero 2W. End-to-end wake reaction per FR-001.

**NFR-002 (Memory, XIAO)** — int8 model ≤ 100 KB; TFLM arena ≤ 150 KB; firmware fits the
XIAO_ESP32S3 flash without disabling the microphone/SD features; wake path must not depend on
PSRAM being present.

**NFR-008 (Observability)** — devices log one prediction line per window (class + confidence) at
debug level; bench produces a markdown table in `docs/bench.md` (spec 004).

**NFR-009 (Actuation safety)** — all relay channels OFF at boot and after reset; arranque (A)
only ever pulses ≤ 2 s and only from a fresh wake → command sequence; no channel ever toggles
without a command event.

**NFR-010 (Feedback latency)** — "Escuchando" playback starts ≤ 200 ms after wake acceptance and
lasts ≤ 500 ms on both targets; end-of-utterance → command-listening stays within FR-001's
budget (≤ 1.5 s XIAO / ≤ 1.0 s Pi).

## Constraints (this milestone)

- Audio output (FR-018): XIAO — MAX98357A I2S DAC/amp + small speaker; Pi — USB audio dongle +
  powered speaker (owner-supplied). "Escuchando" is a repo-committed TTS clip; no runtime TTS.
- Runtime capture mics as in the shared context (XIAO PDM, Pi USB mic).
- Mics and speakers must be physically present before Milestone 3 work starts (PLAN.md hardware logistics).

## Decisions

- **D-002** — Two-stage interaction (wake → command window). *(Fewer false triggers; commands
  alone must not actuate.)*
- **D-006** — Wake acceptance is confirmed audibly with a fixed pre-rendered TTS clip
  ("Escuchando"), the same asset on both targets; no runtime TTS anywhere. *(Owner requested
  spoken confirmation; an MCU can only speak from a bundled clip, and Pi-only synthesis would
  diverge behavior — AGENTS rule 5.)*

## Implementation FAQ

**Q-001: What are the exact Spanish command phrases?**
A: **Resolved.** The registry (`model/registry.json`, Actuation Behavior Contract) is
owner-defined: each action accepts the bare action name and the "enciende" variant. CP is
*"cancelar parqueo"*. Siglas are the registry IDs (`lm lb la gd gi p cp lpb lpa a cc`).

**Q-005: What happens if the user says "hey ultron" twice during the command window?**
A: The window timer refreshes once (5 s again). A second refresh is ignored until an event or
timeout returns to wake-listening.

**Q-010: What actuation hardware in v1?**
A: **Resolved.** 9 relay/driver channels (LM LB LA GD GI LPB LPA A CC) on whichever device
ships — both targets must drive them (the bench decides the winner). XIAO: header GPIOs, PCF8574
expander as documented fallback (TD-009). Pi: libgpiod + 3.3 V relay board.

**Q-011: How does a wiper output stop? (open — resolve before approval)**
A: Default until owner confirms: LPB/LPA are mutually exclusive — selecting one latches it,
selecting the other switches speed; CP stops both wipers as well as parqueo. If the owner wants
wipers to run a fixed time instead, this contract changes before approval.

**Q-012: How does the device say "Escuchando"?**
A: **Resolved.** One Spanish-TTS WAV (16 kHz/16-bit/mono, ≤ 500 ms, silence-trimmed) committed
to the repo; both targets play it: XIAO streams it via I2S to a MAX98357A amp + speaker, the Pi
plays it through a USB audio dongle. Trigger = wake acceptance; the 5 s command window starts at
playback end (FR-019, NFR-010). No other spoken feedback in v1.

## Success Metrics

- **Milestone 3 — "can act"**: both devices pass the two-stage interaction script end-to-end with correct
  output behaviors and feedback timing (FR-001…FR-009, FR-017…FR-019, NFR-010).

## Risks

- **Arduino TFLM library availability/staleness**: the early spike (T10a, spec 002) is the
  canary; documented fallback is vendoring `esp-tflite-micro`.
- **Self-hearing** (the speaker's own clip reaching the mic and skewing scores): windows
  overlapping playback are excluded from decisions (FR-019); the bench (spec 004) runs wake
  sequences with the speaker live.
- **False-wake chatter**: a false wake now speaks. Bounded by NFR-004 (spec 004: ≤ 1 false wake /
  30 min); the bench's false-accept run counts them with playback enabled.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-001–FR-009 and FR-017–FR-019, the Actuation
Behavior Contract, the event contract, and D-002/D-006 in its own words. **Q-011 (wiper stop)
must be resolved before approval.** If any acceptance criterion is ambiguous, ask before
implementing.
