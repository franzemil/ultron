# Ultron — Product Plan

Medium project: one spec folder per major feature. Phases below sequence the work; every phase
ends at a gate (`.status`) before the next begins.

## Feature map

| # | Feature | Spec |
|---|---|---|
| 001 | Ultron Car Manager — voice wake + command pipeline, two targets, bench, EI sync | `specs/001-ultron-car-manager/` |

All v1 scope lives in spec 001. Later features (WiFi control, multi-device, speaker enrollment)
get their own spec folders.

## Phases (spec 001)

| Phase | Goal | Exit criterion |
|---|---|---|
| P0 | Governance: steering docs, spec 001 drafted and approved | `.status` = `tasks:approved` |
| P1 | Data tooling: CLI scaffold, dataset ingest/validate/dcfix, recorders (XIAO + Pi), augmentation | `ultron dataset validate` green on ≥ 30 originals/label |
| P2 | DSP: shared C MFCC + bindings + parity vectors vs EI reference | Per-frame parity test passes (max abs diff ≤ 1e-3) |
| P3 | Model: feature npz builder, PyTorch training, threshold sweep | Fresh-hold-out metrics meet NFR-003 |
| P4 | Export: ONNX → onnx2tf → TFLite int8/float16 + parity report | NFR-007 parity ≥ 99% argmax agreement |
| P5 | Firmware + Pi runtime: state machine, events, actuation | Both devices emit contract events end-to-end |
| P6 | Bench: latency, RAM, false-accepts, confusion, device decision | `docs/bench.md` table complete, winner chosen |
| P7 | Archive: `ultron ei sync`, final docs | Sync verified one-way; docs complete |

## Dependencies

P1 → P2 → P3 → P4 → P5 → P6 → P7. The firmware TFLM spike (T10a) runs **early and parallel** to
P3/P4 because it is the riskiest unknown (Arduino TFLM library availability).

## Milestones

- **M1 "can hear"**: recorders + DSP parity (P1+P2)
- **M2 "can think"**: trained + exported + parity-proven model (P3+P4)
- **M3 "can act"**: both devices run the two-stage loop end-to-end (P5)
- **M4 "can decide"**: benchmark table, device winner (P6)
- **M5 "archived"**: EI sync + docs (P7)
