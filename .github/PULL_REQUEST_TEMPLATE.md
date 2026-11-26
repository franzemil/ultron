<!-- Title format: <type>(<scope>): <summary> — see .agents/skills/conventional-commits -->

## What & why

## Inference-affecting? (required for dataset / DSP / model / threshold / conversion / device-inference changes)

- [ ] No — docs, tooling, or CI only
- [ ] Yes → fill in:

  - Dataset counts per label:
  - Seed / split hash:
  - Pinned versions (torch, onnx, onnx2tf, tensorflow, python):
  - Fresh hold-out metrics:
  - Parity report ≥ 99%:

## Checklist

- [ ] Spec `.status` respected — no implementation before `tasks:approved`
- [ ] Single target (XIAO ESP32S3): no Pi/orangepi code paths reintroduced
- [ ] Docs updated if commands or behaviors changed
