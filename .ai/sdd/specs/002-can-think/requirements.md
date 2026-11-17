# Requirements — 002 Can Think (Milestone 2)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context — read it first), [003-can-act](../003-can-act/) (devices + contracts),
> [004-can-decide](../004-can-decide/) (bench), [005-archived](../005-archived/) (EI sync).
> FR/NFR IDs are **global** across all milestone specs.

## Overview

Milestone 2 gives ultron its **brain**: the feature archive builder, PyTorch training with a threshold
sweep, and the ONNX → TFLite export producing the int8/float16 artifact pair with a hard parity
gate. Training is **incremental**: the initial model trains a 4-class subset (`wake`, `noise`,
`la`, `lb` — TD-012), and each further command lands as one record → train → validate → export
rollout cycle (spec 003 Stage C). A host CLI (`ultron infer`, FR-034–FR-036) runs the exported
artifacts on the laptop — WAV files and live mic — so behavior is verified before any device
work. Everything runs locally and reproducibly; Edge Impulse is not involved (archive-only, spec
005). The early TFLM firmware spike runs here in parallel because it is the riskiest
unknown and gates Milestone 3 planning.

## Goals

1. `ultron features && ultron train run && ultron export tflite` reproduces artifacts from the
   same seed.
2. Fresh hold-out accuracy reported honestly (≥ NFR-003) before any device work.
3. Exported TFLite agrees with PyTorch ≥ 99% argmax on the validation split — or export fails.
4. The exported int8 and float16 artifacts are executable and verifiable on the host via
   `ultron infer` (files + live mic) before any device work.
5. New commands land one rollout cycle at a time (record → train → validate) without
   re-collecting or re-validating the already-approved labels.

## Non-Goals

- No recording/dataset tooling here (spec 001).
- No runtime inference or device behavior here (spec 003) — the artifacts produced here are its
  input.
- No benchmarking here (spec 004).
- The full 11-command phrase set is **not** required to exit this milestone: only the initial
  subset (`la`, `lb` + `wake` + `noise`) is (TD-012); the rest arrive via spec 003 Stage C.

## User Stories

### US-003 — Retrain locally
**As a** device owner
**I want to** run one command sequence to retrain from the dataset
**so that** the model improves without any external service.

**Acceptance criteria:**
- **AC1:** `ultron features && ultron train run && ultron export tflite` reproduces artifacts
  from the same seed.
- **AC2:** The run's metrics, dataset counts, and tool versions land in the PR description.

### US-008 — Add a command incrementally
**As a** device owner
**I want to** add one command at a time via a record → train → validate cycle
**so that** the phrase set grows without re-collecting the whole dataset.

**Acceptance criteria:**
- **AC1:** A record session for the new label plus fresh `wake` and `noise` takes lands in
  `dataset/originals/` and passes `ultron dataset validate` (FR-023–FR-025, spec 001).
- **AC2:** Retraining succeeds with the class set grown by exactly the new label (FR-032), and
  the fresh hold-out report covers the new label.
- **AC3:** Re-export regenerates `build/labels.json` and `model/thresholds.json` including the
  new label (FR-010), with parity still ≥ 99% (NFR-007).

### US-009 — Test the exported model locally
**As a** device owner
**I want to** run the exported TFLite artifacts on the host
**so that** behavior is verified before flashing any device.

**Acceptance criteria:**
- **AC1:** `ultron infer files` scores a WAV (or folder) through both `model_int8.tflite` and
  `model_fp16.tflite`, printing per-class scores and the decoded detection via
  `model/thresholds.json` (FR-034).
- **AC2:** Batch mode over a labeled folder emits a host-TFLite vs PyTorch argmax agreement
  report (FR-036).
- **AC3:** `ultron infer mic` reports wake → command decisions within `window_s`, live on the
  host microphone (FR-035).

## Functional Requirements (EARS, MoSCoW)

**FR-010 (Must)** — WHEN the export pipeline runs, THE SYSTEM SHALL produce from one PyTorch
checkpoint: a full-int8 TFLite (MCU target) and a float16-or-int8 TFLite (Pi target), plus a
parity report.

**FR-011 (Must)** — IF exported-TFLite argmax disagreement with PyTorch exceeds 1% of the
validation split, THE SYSTEM SHALL fail the export and attach the parity report.

**FR-013 (Must)** — WHEN the command registry changes (add/remove/rename),
THE SYSTEM SHALL require retraining before the new phrase set is recognized. (`registry.json`
itself is defined and validated in spec 001, D-005.) The recognized phrase set is the **trained
subset**, not the full registry — a command only becomes recognizable after its rollout cycle
(FR-032; spec 003 Stage C).

**FR-015 (Should)** — WHERE per-class thresholds are configured, THE SYSTEM SHALL apply per-class
threshold and margin values instead of a single global threshold. (The runtime that applies them
is spec 003; this spec produces `model/thresholds.json`, Q-006.)

**FR-032 (Must)** — WHEN training runs, THE SYSTEM SHALL derive the class set from the labels
present in the feature archive — requiring `wake`, `noise`, and at least one command class —
instead of assuming the full registry. Initial trained set: `la`, `lb` (TD-012).

**FR-033 (Must)** — IF the dataset lacks `wake`, `noise`, or at least one command class, THE
SYSTEM SHALL fail training with an error naming the missing labels.

**FR-034 (Must)** — WHEN `ultron infer files` runs with an exported TFLite artifact
(`model_int8.tflite` or `model_fp16.tflite`) and a WAV file or folder, THE SYSTEM SHALL score
each clip through the shared C MFCC path (FR-022, spec 001) and `model/thresholds.json`, printing
per-class scores and the decoded detection.

**FR-035 (Should)** — WHERE `ultron infer mic` is used, THE SYSTEM SHALL score the host
microphone continuously (2 s windows, 500 ms hop, TD-006) and report wake → command decisions
within `window_s` from `model/thresholds.json`, without actuation or spec 003 event semantics.

**FR-036 (Should)** — WHEN `ultron infer files` runs over a labeled folder, THE SYSTEM SHALL emit
a host-TFLite vs PyTorch argmax agreement report for that folder.

## Non-Functional Requirements

**NFR-003 (Accuracy, fresh data)** — on a hold-out set never seen in training/augmentation:
≥ 90% accuracy per **trained** command label and ≥ 90% wake recall. Val-split numbers are reported
only as memorization context (lampara-aliz honesty rule).

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

**Q-013: Which commands must Milestone 2's model recognize?**
A: **Resolved.** The initial trained set is `la` + `lb` — 4 classes with `wake` + `noise`
(FR-032, TD-012); the minimum viable class set is 3 (`wake`, `noise`, 1 command). Remaining
commands land one rollout cycle at a time (spec 003 Stage C).

## Success Metrics

- **Milestone 2 — "can think"**: fresh-hold-out accuracy ≥ NFR-003 on the initial 4-class model
  (`wake`, `noise`, `la`, `lb`); export parity ≥ 99% (NFR-007 export clause); `ultron infer` green
  on both exported artifacts (FR-034, FR-036).

## Risks

- **onnx2tf conversion gaps**: restricted op set + early export smoke test on a dummy checkpoint.
- **Single-speaker overfit**: honest fresh-hold-out reporting; more recordings remain the lever.
- **Initial subset contains a known close pair** (`la`/`lb`, Q-002): deliberate canary — the small
  first model proves the hard case early; more recordings of the pair remain the lever.
- **Close-pair confusion** (luz media/baja/alta; guiñador derecho/izquierdo): measured explicitly
  in the bench (spec 004); mitigation order per Q-002.
- **int8 softmax saturation** (outputs quantize to 1/255 steps, confident predictions cap at
  0.996): known lampara-aliz finding; margins tuned on quantized outputs, not float ones.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-010/011/013/015/032/033/034/035/036,
NFR-003/006/007 (export clause), D-004 (single multiclass model), TD-012 (incremental rollout),
TD-013 (host inference stack), and the shared system context (spec 001) in its own words. If
anything is ambiguous, STOP and ask.
