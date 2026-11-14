# Ultron — Product Plan

Medium project: one spec folder per milestone. Phases below sequence the work; every phase
ends at a gate (`.status`) before the next begins.

## Feature map

| # | Feature (milestone) | Spec |
|---|---|---|
| 001 | Can Hear — CLI, dataset tooling, recorders, MFCC parity | `specs/001-can-hear/` |
| 002 | Can Think — features, training, TFLite export + parity gate | `specs/002-can-think/` |
| 003 | Can Act — firmware + Pi runtime, relays, "Escuchando" feedback | `specs/003-can-act/` |
| 004 | Can Decide — benchmark + device decision | `specs/004-can-decide/` |
| 005 | Archived — EI one-way sync, docs + release | `specs/005-archived/` |

Later features (WiFi control, multi-device, speaker enrollment) get their own spec folders.
Shared system context (constraints, D-decisions, global rules) lives in spec 001.

## Phases (grouped by milestone spec)

| Phase | Goal | Exit criterion | Spec |
|---|---|---|---|
| P0 | Governance: steering docs, specs drafted and approved | `.status` = `tasks:approved` per spec | all |
| P1 | Data tooling: CLI scaffold, dataset ingest/validate/dcfix, recorders (XIAO + Pi), augmentation | `ultron dataset validate` green on ≥ 30 originals/label | 001 |
| P2 | DSP: shared C MFCC + bindings + parity vectors vs EI reference | Per-frame parity test passes (max abs diff ≤ 1e-3) | 001 |
| P3 | Model: feature npz builder, PyTorch training, threshold sweep | Fresh-hold-out metrics meet NFR-003 | 002 |
| P4 | Export: ONNX → onnx2tf → TFLite int8/float16 + parity report | NFR-007 parity ≥ 99% argmax agreement | 002 |
| P5 | Firmware + Pi runtime: state machine, events, actuation, "Escuchando" wake feedback | Both devices emit contract events end-to-end; feedback timing meets NFR-010 | 003 |
| P6 | Bench: latency, RAM, false-accepts, confusion, device decision | `docs/bench.md` table complete, winner chosen | 004 |
| P7 | Archive: `ultron ei sync`, final docs | Sync verified one-way; docs complete | 005 |

## Dependencies

P1 → P2 → P3 → P4 → P5 → P6 → P7. The firmware TFLM spike (T10a, spec 002) runs **early and
parallel** to P3/P4 because it is the riskiest unknown (Arduino TFLM library availability).

**Hardware logistics**: the Pi USB microphone must be on hand before P1's recording runs; the
MAX98357A I2S amp + speaker (XIAO) and the USB audio dongle + powered speaker (Pi) must be on
hand before P5 (T15, "Escuchando").

## Milestones

- **M1 "can hear"** (spec 001): recorders + DSP parity (P1+P2)
- **M2 "can think"** (spec 002): trained + exported + parity-proven model (P3+P4)
- **M3 "can act"** (spec 003): both devices run the two-stage loop end-to-end + feedback (P5)
- **M4 "can decide"** (spec 004): benchmark table, device winner (P6)
- **M5 "archived"** (spec 005): EI sync + docs (P7)
