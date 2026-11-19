# Ultron — Product Plan

Medium project: one spec folder per milestone. Milestones below sequence the work; every
milestone ends at a gate (`.status`) before the next begins. Governance (steering docs, specs
drafted and approved) gates every milestone start.

## Feature map

| # | Feature (milestone) | Spec |
|---|---|---|
| 001 | Can Hear — CLI, dataset tooling, MFCC parity (Milestone 1; recorders moved to 003) | `specs/001-can-hear/` |
| 002 | Can Think — features, training, TFLite export + parity gate (Milestone 2) | `specs/002-can-think/` |
| 003 | Can Act — firmware + Pi runtime, SuperMini relays, audio confirmations, recorders (Milestone 3) | `specs/003-can-act/` |
| 004 | Can Decide — benchmark + device decision (Milestone 4) | `specs/004-can-decide/` |
| 005 | Archived — EI one-way sync, docs + release (Milestone 5) | `specs/005-archived/` |

Later features (WiFi control, multi-device, speaker enrollment) get their own spec folders.
Shared system context (constraints, D-decisions, global rules) lives in spec 001.

## Milestones (the plan)

| Milestone | Goal | Exit criterion | Spec |
|---|---|---|---|
| **Milestone 1 — "can hear"** | Data tooling: CLI scaffold, dataset ingest/validate/dcfix (external recordings — device recorders moved to M3), augmentation; DSP: shared C MFCC + bindings + parity vectors vs EI reference | `ultron dataset validate` green on ≥ 30 originals/label; per-frame parity test passes (max abs diff ≤ 1e-3) | 001 |
| **Milestone 2 — "can think"** | Model: feature npz builder, PyTorch training, threshold sweep; Export: ONNX → onnx2tf → TFLite int8/float16 + parity report | Fresh-hold-out metrics meet NFR-003; NFR-007 parity ≥ 99% argmax agreement | 002 |
| **Milestone 3 — "can act"** | Firmware + Pi runtime: state machine, events, actuation (companion ESP32-S3 SuperMini on the XIAO stack), audio confirmations ("Escuchando" + "comando"), WAV recorders (moved from M1); staged bring-up: console-only → audio + 2 relays → full companion stack | Both devices emit contract events end-to-end; audio + feedback timing meet NFR-010/FR-023; recorders produce valid WAVs (US-002) | 003 |
| **Milestone 4 — "can decide"** | Bench: latency, RAM, false-accepts, confusion, device decision | `docs/bench.md` table complete, winner chosen | 004 |
| **Milestone 5 — "archived"** | Archive: `ultron ei sync`, final docs | Sync verified one-way; docs complete | 005 |

## Dependencies

Milestone 1 → 2 → 3 → 4 → 5. The firmware TFLM spike (T10a, spec 002) runs **early and
parallel** inside Milestone 2 because it is the riskiest unknown (Arduino TFLM library
availability) and its outcome gates Milestone 3 planning.

**Hardware logistics**: the Pi USB microphone must be on hand before Milestone 3 (runtime
capture + recorder); the MAX98357A I2S amp + speaker (XIAO) and the USB audio dongle + powered
speaker (Pi) must be on hand before Milestone 3 (T15, "Escuchando").
