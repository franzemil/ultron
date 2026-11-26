# Design — 002 Can Think (Milestone 2)

## Executive summary

One PyTorch keyword-spotting model — initially recognizing `wake` ("hey ultron"), `noise`,
"luz alta" (`la`) and "luz baja" (`lb`) — is trained on the Milestone 1 feature archive with
class weights and early stopping. Each further command lands as one record → train → validate →
export rollout cycle (spec 003 Stage C). The operating point is chosen by a threshold sweep
against the fresh hold-out, then the model is exported once via ONNX → onnx2tf to a full-int8
TFLite (MCU) and a float16-or-int8 TFLite (Pi) with a parity report that fails the build below
99% argmax agreement. A host CLI (`ultron infer`) executes the exported artifacts on the laptop
— WAV files and live mic — before any device work.

## Requirements mapping

| FR / NFR | Design section |
|---|---|
| FR-010, FR-011 | [Export pipeline](#export-pipeline), NFR-007 check |
| FR-013, FR-015 | [Data model](#data-model) (thresholds.json), [Model](#model) (sweep) |
| FR-032, FR-033 | [Data model](#data-model) (class-set derivation), TD-012 |
| FR-034, FR-035, FR-036 | [Host inference](#host-inference), TD-013 |
| NFR-003 | [Model](#model) (hold-out scoring), [Verification](#verification-strategy) |
| NFR-006 | [Conventions](../../../.ai/steering/conventions.md) (seeds, changelog) |
| NFR-007 (export clause) | [Export pipeline](#export-pipeline) |

## Data model additions (beyond spec 001)

`model/thresholds.json` (versioned; Q-006): `{"wake": 0.80, "margin_wake": 0.10,
"commands": {"lm": {"threshold": 0.80, "margin": 0.10}, "…": "…"}, "window_s": 5.0}` — one entry
per **trained** command only (FR-032).

`build/features.npz` (built by T7, MFCC from spec 001's shared C library): float32 features +
labels + file paths. The initial label set is **4 labels**: `wake`, `noise`, `la` (luz alta) and
`lb` (luz baja); each rollout cycle adds its command label (FR-032/033, TD-012). Class index
order is frozen into each export and the device firmware (same array order everywhere; re-frozen
on every retrain). Phrase variants share one class per command (TD-010).

## Model

PyTorch (`model/model.py`) — input `(100, 13)`, restricted op set (Q-008):

```
Conv1d(13 → 16, k=3, relu) → MaxPool1d(2)
Conv1d(16 → 32, k=3, relu) → GlobalAvgPool1d
Linear(32 → 16, relu) → Linear(16 → C, softmax)     # C = output classes (initially: wake, noise, la, lb)
```

Training (`model/train.py`): Adam 1e-3, sparse categorical cross-entropy, class weights for
imbalance, batch 16, early stopping (patience 30), stratified 90/10 split seeded 42, up to 300
epochs. Fresh hold-out is scored after training and reported separately (NFR-003).

Threshold sweep (`ultron train sweep`): grid over threshold ∈ {0.6…0.95} × margin ∈ {0.05…0.20}
against the hold-out, writing `model/thresholds.json` (Q-006) with the operator-chosen operating
point. Entries are written for trained commands only (FR-032).

## Export pipeline

`model/export.py` + `ultron export tflite` (FR-010):

1. `torch.onnx.export` (opset ~17) → `build/model.onnx`
2. `onnx2tf` → float TFLite (TensorFlow is a build-time-only dependency)
3. Quantize: **full-int8** (`TFLITE_BUILTINS_INT8`, int8 in/out) with training features as the
   representative dataset → `build/model_int8.tflite` (XIAO)
4. ~~**float16** → `build/model_fp16.tflite` (Pi)~~ — withdrawn 2026-11-26 (single target: int8 only)
5. **Parity report** (`build/parity_report.md`): PyTorch vs int8 argmax agreement on the val
   split — must be ≥ 99% or the export **fails** (FR-011, NFR-007)
6. Label order (trained subset only, FR-032) echoed into the report and into `build/labels.json`

## Host inference

`ultron infer` (FR-034–FR-036, TD-013) — the exported artifacts are executable and verifiable on
the host before any device work:

- `ultron infer files --model <tflite> --wav <file|folder>` — 2 s windows / 500 ms hop through
  the shared C MFCC library (FR-022, spec 001), scores decoded via `model/thresholds.json`;
  prints per-class scores and the detected label.
- `ultron infer mic --model <tflite>` — continuous host-mic scoring; reports wake → command
  decisions within `window_s`. No actuation and no spec 003 event semantics — the device state
  machine has exactly one owner (spec 003).
- Batch over a labeled folder emits a host-TFLite vs PyTorch argmax agreement report (FR-036) —
  the runnable, on-demand complement of the NFR-007 gate.

Runtime is `tflite_runtime`/LiteRT; TensorFlow stays build-time-only (TD-002).

## Technical decisions

**TD-001 — Single multiclass model + firmware state machine** (D-004). Alternatives: two
dedicated models (wake-net with hard negatives + command-net) — better precision at the cost of
two artifacts and 2× inference; rejected for v1 simplicity, revisit if NFR-004 (spec 004) fails.

**TD-002 — Conversion via `torch.onnx.export` → onnx2tf → TFLite.** Alternatives: re-author
training in TensorFlow like lampara-aliz (rejected: TensorFlow-first contradicts the project
goal); torchscript/executorch (rejected: less mature int8 full-quant MCU story). TensorFlow stays
a build-time-only dependency of onnx2tf.

**TD-010 — Phrase variants merge into one class per command.** Reason: one class per command
keeps the class count at the registry minimum (cap 13); variants differ mainly by the leading
verb, and class weights + augmentation absorb the diversity. Alternative: one class per variant (21 classes) — more precision but more data
per variant and a larger model; revisit only if the bench shows variant-induced confusion.

**TD-012 — Incremental command rollout (subset training).** The initial model trains on `wake`,
`noise`, `la` and `lb`; each further command lands as one record → train → validate → export →
redeploy cycle (spec 003 Stage C tasks). Training requires at least `wake`, `noise` and one
command (FR-032/033). *(Owner wants a deployable, locally testable model before collecting data
for the full command set; each new command is verified end-to-end, one cycle at a time.)*
Alternative rejected: one-shot training on every command — far more upfront data collection
before the first deployable model and no per-command validation loop.

**TD-013 — Host inference via `tflite_runtime` + shared MFCC binding.** The host test path
executes the exact exported flatbuffers (int8 + float16) through the same C MFCC library as the
devices (FR-022, spec 001) — no PyTorch/NumPy shortcut — so what is validated locally is what
ships. Alternatives: PyTorch-only host checks (rejected: does not exercise the quantized
artifact); a full host-side state machine (rejected: duplicates spec 003 semantics — a third
implementation to keep in sync).

## Risks & mitigations

- **onnx2tf conversion gap** → op set restricted (Q-008); smoke-test export on a randomly
  initialized model early (task T9's first checklist item).
- **int8 softmax saturation** (outputs quantize to 1/255 steps, confident predictions cap at
  0.996) → known lampara-aliz finding; margins tuned on quantized outputs, not float ones.
- **Single-speaker overfit** → honest fresh-hold-out reporting (NFR-003); more recordings remain
  the lever (spec 001 pipeline).
- **Close-pair confusion** → measured explicitly in the bench (spec 004); mitigation order per
  Q-002. The initial subset already contains one (`la`/`lb`) — deliberate canary (TD-012).

## Verification strategy

| Level | What | How |
|---|---|---|
| Unit | Reproducibility | same seed ⇒ identical split hashes + metrics (NFR-006) |
| Integration | Export | parity report ≥ 99% (FR-011); `labels.json` order check |
| Integration | Features | `ultron features` on fixture set; re-run ⇒ identical file hash |
| Integration | Host inference | `ultron infer files` on labeled fixtures: scores + agreement report (FR-036); corrupted artifact fails loudly (FR-034) |
| Smoke | Training | 2-epoch smoke run on the initial-label fixture set (`wake`, `noise`, `la`, `lb`) completes; hold-out report generated |
