# Requirements — 001 Ultron Car Manager

## Overview

A small always-listening voice device that wakes on the Spanish-spoken phrase **"hey ultron"** and
then recognizes a short Spanish command from a fixed registry of **car actuations** — 10 actions
(luces, guiñadores, parqueo, limpia parabrisas, arranque, cierre centralizado) plus a cancel
command (CP), driving **9 relay channels** (LM, LB, LA, GD, GI, LPB, LPA, A, CC). Recognition
runs entirely on one of
two candidate devices — the pipeline is built once and deployed to **both** (Seeed XIAO ESP32S3
Sense and Orange Pi Zero 2W); a repeatable benchmark then decides which device ships. Models are
trained locally (PyTorch as source of truth) and exported to TFLite for both targets. Edge Impulse
receives the dataset and model as an archive — one-way.

## Goals

1. Reliable two-stage interaction: wake phrase → audible "Escuchando" confirmation → 5 s command window → event (or timeout).
2. One training pipeline (PyTorch → TFLite) producing artifacts for both targets from a single
   checkpoint, with proven train-vs-device numerical parity.
3. An honest, repeatable benchmark comparing both devices (latency, memory, false accepts,
   per-command accuracy) to select the deployment device.
4. Retraining loop fully local and reproducible; Edge Impulse used only as a one-way archive.

## Non-Goals (v1)

- **No general speech recognition / ASR** — fixed-phrase classification only (device purpose).
- **No cloud inference at runtime** — privacy and latency; inference is fully on-device.
- **No multi-device / multi-room coordination** — exactly one device will be deployed (evaluation
  uses both, sequentially).
- **No multi-speaker enrollment** — the owner's voice is the primary target (additional speakers
  are a data bonus, not a feature).
- **No WiFi/remote/network control of the device in v1** — actuation is local (relay/GPIO or
  local webhook out of the Pi).
- **No runtime phrase teaching** — registry changes require retraining + redeploying.
- **No OTA firmware updates** — flashing via USB like lampara-aliz.
- **No continuous audio recording/storage** — audio is processed in transient buffers and discarded.

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

### US-002 — Collect training data on either device
**As a** device owner
**I want to** record WAV clips of each phrase (and noise) from either candidate device
**so that** the model trains on the real microphones it will run on.

**Acceptance criteria:**
- **AC1:** The XIAO recorder writes 16 kHz/16-bit/mono WAVs labeled by phrase to storage.
- **AC2:** The Pi recorder captures the same format from its microphone.
- **AC3:** Every recording is DC-offset-correctable and validates (`ultron dataset validate`).

### US-003 — Retrain locally
**As a** device owner
**I want to** run one command sequence to retrain from the dataset
**so that** the model improves without any external service.

**Acceptance criteria:**
- **AC1:** `ultron features && ultron train run && ultron export tflite` reproduces artifacts
  from the same seed.
- **AC2:** The run's metrics, dataset counts, and tool versions land in the PR description.

### US-004 — Deploy the identical model to both devices
**As a** device owner
**I want to** deploy the same exported model to XIAO and Pi
**so that** the benchmark compares devices, not models.

**Acceptance criteria:**
- **AC1:** Both devices load the same `.tflite` (int8 on XIAO; int8/float16 on Pi).
- **AC2:** Both devices emit identical event types/semantics for the same audio.

### US-005 — Benchmark and pick the device
**As a** device owner
**I want to** run the comparison bench
**so that** the device choice is backed by measured data.

**Acceptance criteria:**
- **AC1:** `docs/bench.md` contains latency, memory, false-accepts, and per-command confusion
  for both devices on the same test protocol.
- **AC2:** The winner is declared in `docs/bench.md` with the numbers that justify it.

### US-006 — Archive to Edge Impulse
**As a** device owner
**I want to** sync the dataset and current model to Edge Impulse
**so that** the work is archived on the platform from the previous project.

**Acceptance criteria:**
- **AC1:** `ultron ei sync` uploads dataset + model artifacts to the configured EI project.
- **AC2:** Local files are byte-identical before and after the sync (one-way).

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
types and semantics on both targets).

**FR-009 (Must)** — WHEN a command event is emitted, THE SYSTEM SHALL drive its mapped relay
channel(s) exactly as defined in the Actuation Behavior Contract, within timing tolerances
(blink ±100 ms, pulses ±50 ms, CP stop < 200 ms).

**FR-010 (Must)** — WHEN the export pipeline runs, THE SYSTEM SHALL produce from one PyTorch
checkpoint: a full-int8 TFLite (MCU target) and a float16-or-int8 TFLite (Pi target), plus a
parity report.

**FR-011 (Must)** — IF exported-TFLite argmax disagreement with PyTorch exceeds 1% of the
validation split, THE SYSTEM SHALL fail the export and attach the parity report.

**FR-012 (Should)** — WHEN `ultron ei sync` is invoked, THE SYSTEM SHALL upload the dataset and
current model artifacts to the configured Edge Impulse project without modifying any local
artifact.

**FR-013 (Must)** — WHEN the command registry changes (add/remove/rename),
THE SYSTEM SHALL require retraining before the new phrase set is recognized.

**FR-014 (Should)** — WHILE ambient audio persists, THE SYSTEM SHALL count and report wake
false-accepts and command false-events for the bench report.

**FR-015 (Should)** — WHERE per-class thresholds are configured, THE SYSTEM SHALL apply per-class
threshold and margin values instead of a single global threshold.

**FR-016 (Must)** — THE SYSTEM SHALL support a registry of up to 10 action commands plus a
cancel command (CP), plus one wake class and noise — v1 hard cap: 13 model classes.

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

**NFR-003 (Accuracy, fresh data)** — on a hold-out set never seen in training/augmentation:
≥ 90% accuracy per command label and ≥ 90% wake recall. Val-split numbers are reported only as
memorization context (lampara-aliz honesty rule).

**NFR-004 (False accepts)** — during a 30-minute ambient bench: ≤ 1 false wake and 0 command
events while in wake-listening state.

**NFR-005 (Data volume)** — ≥ 20 original recordings per accepted phrase variant (2 variants per
action command, 1 for CP → 21 variants; wake and noise ≥ 30 each), plus ≥ 4 seeded augmentations
per original; hold-out ≥ 8 originals per variant. Batch-recorded 10 s clips split per utterance
count toward the totals.

**NFR-006 (Reproducibility)** — any reported result reproducible from seed (42) + dataset counts +
pinned tool versions recorded in the **PR description** (CHANGELOG.md is generated from PR
merges, never hand-edited).

**NFR-007 (Parity)** — exported TFLite argmax agreement with PyTorch ≥ 99% on the validation
split; DSP per-frame parity vs reference vectors ≤ 1e-3 max abs diff (float).

**NFR-008 (Observability)** — devices log one prediction line per window (class + confidence) at
debug level; bench produces a markdown table in `docs/bench.md`.

**NFR-009 (Actuation safety)** — all relay channels OFF at boot and after reset; arranque (A)
only ever pulses ≤ 2 s and only from a fresh wake → command sequence; no channel ever toggles
without a command event.

**NFR-010 (Feedback latency)** — "Escuchando" playback starts ≤ 200 ms after wake acceptance and
lasts ≤ 500 ms on both targets; end-of-utterance → command-listening stays within FR-001's
budget (≤ 1.5 s XIAO / ≤ 1.0 s Pi).

## Constraints

- Wake phrase is fixed: **"hey ultron"**. Command registry is owner-defined and resolved (Q-001):
  10 car actions + CP, each accepting the bare action name and the "enciende" variant. Longest
  phrase ("enciende limpia parabrisas baja") fits the 2 s analysis window.
- **All voice interaction is Spanish** (v1 hard constraint): every command utterance is spoken in
  Spanish; "hey ultron" is a fixed acoustic token spoken the way the owner says it (its English
  spelling is irrelevant — only its sound matters). No English or multilingual phrases are
  recognized, ever.
- Primary speaker: the owner. Primary room: the deployment room.
- XIAO mic: built-in PDM. Pi mic: owner-supplied USB microphone.
- Audio output (FR-018): XIAO — MAX98357A I2S DAC/amp + small speaker; Pi — USB audio dongle +
  powered speaker (owner-supplied). "Escuchando" is a repo-committed TTS clip; no runtime TTS.
- Dev host: macOS arm64; Pi OS: Debian 12 aarch64.
- Edge Impulse account and API key already available (from lampara-aliz workflow).

## Decisions

- **D-001** — Two candidate devices, one pipeline, benchmark decides the winner. *(Owner wants
  measured comparison rather than assuming the MCU is enough.)*
- **D-002** — Two-stage interaction (wake → command window). *(Fewer false triggers; commands
  alone must not actuate.)*
- **D-003** — Local PyTorch is the source of truth; Edge Impulse is one-way export/archive.
- **D-004** — v1 uses one multiclass model (`wake`, `noise`, and the 11 command classes
  `lm lb la gd gi p cp lpb lpa a cc`) driving a firmware state machine; splitting into dedicated
  wake/command models is a fallback if false-accept targets fail. *(Revisit trigger: NFR-004
  miss.)*
- **D-005** — Command registry lives in a versioned config file (`model/registry.json`) with
  stable IDs; phrase edits are registry changes per FR-013.

- **D-006** — Wake acceptance is confirmed audibly with a fixed pre-rendered TTS clip
  ("Escuchando"), the same asset on both targets; no runtime TTS anywhere. *(Owner requested
  spoken confirmation; an MCU can only speak from a bundled clip, and Pi-only synthesis would
  diverge behavior — AGENTS rule 5.)*

## Implementation FAQ

**Q-001: What are the exact Spanish command phrases?**
A: **Resolved.** The registry (Actuation Behavior Contract) is owner-defined: each action accepts
the bare action name and the "enciende" variant (e.g. *"luz media"* / *"enciende luz media"*).
CP is *"cancelar parqueo"*. Siglas are the registry IDs (`lm lb la gd gi p cp lpb lpa a cc`).

**Q-002: What about acoustically close commands (luz media/baja/alta; guiñador
derecho/izquierdo)?**
A: Accepted; the bench measures those exact confusion pairs. If a pair stays above the margin
error after threshold tuning, mitigation order is: more recordings of the confused pair →
per-class thresholds (FR-015) → owner revises phrases (FR-013).

**Q-003: Which mic on the Pi? What if none is present?**
A: Owner-supplied USB mic; runtime fails fast listing detected capture devices and exits. No
fallback to "assume device 0".

**Q-004: Both devices in the same room during bench?**
A: Bench runs sequentially; only one device is listening at a time to avoid cross-triggering.

**Q-005: What happens if the user says "hey ultron" twice during the command window?**
A: The window timer refreshes once (5 s again). A second refresh is ignored until an event or
timeout returns to wake-listening.

**Q-006: Where do thresholds live and how are they tuned?**
A: `model/thresholds.json` (wake threshold, per-command threshold+margin), tuned by the training
pipeline's threshold sweep; defaults wake=0.80, cmd=0.80, margin=0.10. Threshold changes are
PR-documented events.

**Q-007: What happens on DC offset / NaN features?**
A: `dataset dcfix` corrects DC offset before features; any NaN feature row fails the pipeline
loudly (no silent training on corrupt data — lampara lesson).

**Q-008: What if onnx2tf can't convert an op?**
A: Model architecture is restricted to Conv1d/Dense/pooling/softmax (all supported); documented
fallback: reshape Conv1d to Conv2d. If still blocked, STOP and ask — no architecture freelancing.

**Q-009: Which Edge Impulse project receives the sync?**
A: A dedicated EI project for ultron (not the lampara project); `EI_KEY` and project id in `.env`.

**Q-010: What actuation hardware in v1?**
A: **Resolved.** 9 relay/driver channels (LM LB LA GD GI LPB LPA A CC) on whichever device
ships — both targets must drive them (the bench decides the winner). XIAO: header GPIOs, PCF8574
expander as documented fallback (design TD-009). Pi: libgpiod + 3.3 V relay board.

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

- M1: both recorders + DSP parity green (NFR-007 DSP clause).
- M2: fresh-hold-out accuracy ≥ NFR-003 on the initial model.
- M3: both devices pass the two-stage interaction script end-to-end with correct output
  behaviors (FR-001…FR-019).
- M4: bench table complete; device winner declared with justification (US-005).
- M5: EI sync verified one-way (FR-012); docs complete.

## Risks

- **Mic domain mismatch** (PDM vs USB mic sound different → model biased to XIAO audio):
  mitigate by recording from both mics and augmenting; bench measures per-device accuracy.
- **Arduino TFLM library availability/staleness**: early spike (T10a) is the canary; documented
  fallback is vendoring `esp-tflite-micro`.
- **onnx2tf conversion gaps**: restricted op set + early export smoke test on a dummy checkpoint.
- **Single-speaker overfit**: honest fresh-hold-out reporting; more recordings remain the lever.
- **Close-pair confusion** (luz media/baja/alta; guiñador derecho/izquierdo): measured explicitly
  in the bench; mitigation order per Q-002.
- **Self-hearing** (the speaker's own clip reaching the mic and skewing scores): windows
  overlapping playback are excluded from decisions (FR-019); the bench runs wake sequences with
  the speaker live.
- **False-wake chatter**: a false wake now speaks. Bounded by NFR-004 (≤ 1 false wake / 30 min);
  the bench's false-accept run counts them with playback enabled.

## Confirm before building

No implementation code until `.status` reads `tasks:approved`. Before starting implementation the
implementing agent restates FR-001–FR-019, the Actuation Behavior Contract, the event contract,
and D-004/D-006 in its own words. If Q-011 (wiper stop) or any acceptance criterion is ambiguous,
ask before implementing.
