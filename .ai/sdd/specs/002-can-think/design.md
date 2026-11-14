# Design — 002 Can Think (Milestone M2)

## Executive summary

One PyTorch keyword-spotting model — 13 classes (`wake`, `noise`, and the 11 command classes
`lm lb la gd gi p cp lpb lpa a cc`) — is trained on the M1 feature archive with class weights and
early stopping, its operating point chosen by a threshold sweep against the fresh hold-out, then
exported once via ONNX → onnx2tf to a full-int8 TFLite (MCU) and a float16-or-int8 TFLite (Pi)
with a parity report that fails the build below 99% argmax agreement.

## Requirements mapping

| FR / NFR | Design section |
|---|---|
| FR-010, FR-011 | [Export pipeline](#export-pipeline), NFR-007 check |
| FR-013, FR-015 | [Data model](#data-model) (thresholds.json), [Model](#model) (sweep) |
| NFR-003 | [Model](#model) (hold-out scoring), [Verification](#verification-strategy) |
| NFR-006 | [Conventions](../../../.ai/steering/conventions.md) (seeds, changelog) |
| NFR-007 (export clause) | [Export pipeline](#export-pipeline) |

## Data model additions (beyond spec 001)

`model/thresholds.json` (versioned; Q-006): `{"wake": 0.80, "margin_wake": 0.10,
"commands": {"lm": {"threshold": 0.80, "margin": 0.10}, "…": "…"}, "window_s": 5.0}` — one entry
per command class.

`build/features.npz` (built by T7, MFCC from spec 001's shared C library): float32 features +
labels + file paths. Label set = sorted registry ids + `wake` + `noise`; class index order is
frozen into the export and the device firmware (same array order everywhere). Phrase variants
share one class per command (TD-010).

## Model

PyTorch (`model/model.py`) — input `(100, 13)`, restricted op set (Q-008):

```
Conv1d(13 → 16, k=3, relu) → MaxPool1d(2)
Conv1d(16 → 32, k=3, relu) → GlobalAvgPool1d
Linear(32 → 16, relu) → Linear(16 → C, softmax)     # C = 13 (wake, noise, 11 commands)
```

Training (`model/train.py`): Adam 1e-3, sparse categorical cross-entropy, class weights for
imbalance, batch 16, early stopping (patience 30), stratified 90/10 split seeded 42, up to 300
epochs. Fresh hold-out is scored after training and reported separately (NFR-003).

Threshold sweep (`ultron train sweep`): grid over threshold ∈ {0.6…0.95} × margin ∈ {0.05…0.20}
against the hold-out, writing `model/thresholds.json` (Q-006) with the operator-chosen operating
point.

## Export pipeline

`model/export.py` + `ultron export tflite` (FR-010):

1. `torch.onnx.export` (opset ~17) → `build/model.onnx`
2. `onnx2tf` → float TFLite (TensorFlow is a build-time-only dependency)
3. Quantize: **full-int8** (`TFLITE_BUILTINS_INT8`, int8 in/out) with training features as the
   representative dataset → `build/model_int8.tflite` (XIAO)
4. **float16** → `build/model_fp16.tflite` (Pi); int8 also offered for the Pi
5. **Parity report** (`build/parity_report.md`): PyTorch vs int8 argmax agreement on the val
   split — must be ≥ 99% or the export **fails** (FR-011, NFR-007)
6. Label order echoed into the report and into `build/labels.json`

## Technical decisions

**TD-001 — Single multiclass model + firmware state machine** (D-004). Alternatives: two
dedicated models (wake-net with hard negatives + command-net) — better precision at the cost of
two artifacts and 2× inference; rejected for v1 simplicity, revisit if NFR-004 (spec 004) fails.

**TD-002 — Conversion via `torch.onnx.export` → onnx2tf → TFLite.** Alternatives: re-author
training in TensorFlow like lampara-aliz (rejected: TensorFlow-first contradicts the project
goal); torchscript/executorch (rejected: less mature int8 full-quant MCU story). TensorFlow stays
a build-time-only dependency of onnx2tf.

**TD-010 — Phrase variants merge into one class per command.** Reason: 13 classes keeps the
model tiny; variants differ mainly by the leading verb, and class weights + augmentation absorb
the diversity. Alternative: one class per variant (21 classes) — more precision but more data
per variant and a larger model; revisit only if the bench shows variant-induced confusion.

## Risks & mitigations

- **onnx2tf conversion gap** → op set restricted (Q-008); smoke-test export on a randomly
  initialized model early (task T9's first checklist item).
- **int8 softmax saturation** (outputs quantize to 1/255 steps, confident predictions cap at
  0.996) → known lampara-aliz finding; margins tuned on quantized outputs, not float ones.
- **Single-speaker overfit** → honest fresh-hold-out reporting (NFR-003); more recordings remain
  the lever (spec 001 pipeline).
- **Close-pair confusion** → measured explicitly in the bench (spec 004); mitigation order per
  Q-002.

## Verification strategy

| Level | What | How |
|---|---|---|
| Unit | Reproducibility | same seed ⇒ identical split hashes + metrics (NFR-006) |
| Integration | Export | parity report ≥ 99% (FR-011); `labels.json` order check |
| Integration | Features | `ultron features` on fixture set; re-run ⇒ identical file hash |
| Smoke | Training | 2-epoch smoke run on fixtures completes; hold-out report generated |
