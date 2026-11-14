# Requirements — 002 Can Think (Milestone 2)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context — read it first), [003-can-act](../003-can-act/) (devices + contracts),
> [004-can-decide](../004-can-decide/) (bench), [005-archived](../005-archived/) (EI sync).
> FR/NFR IDs are **global** across all milestone specs.

## Overview

Milestone 2 gives ultron its **brain**: the feature archive builder, PyTorch training with a threshold
sweep, and the ONNX → TFLite export producing the int8/float16 artifact pair with a hard parity
gate. Everything runs locally and reproducibly; Edge Impulse is not involved (archive-only, spec
005). The early TFLM firmware spike (T10a) runs here in parallel because it is the riskiest
unknown and gates Milestone 3 planning.

## Goals

1. `ultron features && ultron train run && ultron export tflite` reproduces artifacts from the
   same seed.
2. Fresh hold-out accuracy reported honestly (≥ NFR-003) before any device work.
3. Exported TFLite agrees with PyTorch ≥ 99% argmax on the validation split — or export fails.

## Non-Goals

- No recording/dataset tooling here (spec 001).
- No runtime inference or device behavior here (spec 003) — the artifacts produced here are its
  input.
- No benchmarking here (spec 004).

## User Stories

### US-003 — Retrain locally
**As a** device owner
**I want to** run one command sequence to retrain from the dataset
**so that** the model improves without any external service.

**Acceptance criteria:**
- **AC1:** `ultron features && ultron train run && ultron export tflite` reproduces artifacts
  from the same seed.
- **AC2:** The run's metrics, dataset counts, and tool versions land in the PR description.

## Functional Requirements (EARS, MoSCoW)

**FR-010 (Must)** — WHEN the export pipeline runs, THE SYSTEM SHALL produce from one PyTorch
checkpoint: a full-int8 TFLite (MCU target) and a float16-or-int8 TFLite (Pi target), plus a
parity report.

**FR-011 (Must)** — IF exported-TFLite argmax disagreement with PyTorch exceeds 1% of the
validation split, THE SYSTEM SHALL fail the export and attach the parity report.

**FR-013 (Must)** — WHEN the command registry changes (add/remove/rename),
THE SYSTEM SHALL require retraining before the new phrase set is recognized. (`registry.json`
itself is defined and validated in spec 001, D-005.)

**FR-015 (Should)** — WHERE per-class thresholds are configured, THE SYSTEM SHALL apply per-class
threshold and margin values instead of a single global threshold. (The runtime that applies them
is spec 003; this spec produces `model/thresholds.json`, Q-006.)

## Non-Functional Requirements

**NFR-003 (Accuracy, fresh data)** — on a hold-out set never seen in training/augmentation:
≥ 90% accuracy per command label and ≥ 90% wake recall. Val-split numbers are reported only as
memorization context (lampara-aliz honesty rule).

**NFR-006 (Reproducibility)** — any reported result reproducible from seed (42) + dataset counts +
pinned tool versions recorded in the **PR description** (CHANGELOG.md is generated from PR
merges, never hand-edited).

**NFR-007 (Parity, export clause)** — exported TFLite argmax agreement with PyTorch ≥ 99% on the
validation split. The DSP clause of NFR-007 lives in spec 001.

## Implementation FAQ

**Q-002: What about acoustically close commands (luz media/baja/alta; guiñador
derecho/izquierdo)?**
A: Accepted; the bench (spec 004) measures those exact confusion pairs. If a pair stays above the
margin error after threshold tuning, mitigation order is: more recordings of the confused pair →
per-class thresholds (FR-015) → owner revises phrases (FR-013).

**Q-006: Where do thresholds live and how are they tuned?**
A: `model/thresholds.json` (wake threshold, per-command threshold+margin), tuned by the training
pipeline's threshold sweep; defaults wake=0.80, cmd=0.80, margin=0.10. Threshold changes are
PR-documented events.

**Q-008: What if onnx2tf can't convert an op?**
A: Model architecture is restricted to Conv1d/Dense/pooling/softmax (all supported); documented
fallback: reshape Conv1d to Conv2d. If still blocked, STOP and ask — no architecture freelancing.

## Success Metrics

- **Milestone 2 — "can think"**: fresh-hold-out accuracy ≥ NFR-003 on the initial model; export parity ≥ 99%
  (NFR-007 export clause).

## Risks

- **onnx2tf conversion gaps**: restricted op set + early export smoke test on a dummy checkpoint.
- **Single-speaker overfit**: honest fresh-hold-out reporting; more recordings remain the lever.
- **Close-pair confusion** (luz media/baja/alta; guiñador derecho/izquierdo): measured explicitly
  in the bench (spec 004); mitigation order per Q-002.
- **int8 softmax saturation** (outputs quantize to 1/255 steps, confident predictions cap at
  0.996): known lampara-aliz finding; margins tuned on quantized outputs, not float ones.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-010/011/013/015, NFR-003/006/007 (export
clause), D-004 (single multiclass model), and the shared system context (spec 001) in its own
words. If anything is ambiguous, STOP and ask.
