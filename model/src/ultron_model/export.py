"""ONNX → TFLite export + parity gate — T9 (spec 002-can-think, FR-010/FR-011).

Pipeline (design.md §Export pipeline, TD-002):

1. ``torch.onnx.export`` (opset 17, legacy exporter) → ``build/model.onnx``
2. ``onnx2tf`` → float SavedModel (TensorFlow is build-time-only)
3. full-int8 quantization (``TFLITE_BUILTINS_INT8``, int8 in/out) with training
   features as the representative dataset → ``build/model_int8.tflite`` (XIAO)
4. float16 → ``build/model_fp16.tflite`` (Pi)
5. parity report ``build/parity_report.md`` — PyTorch vs int8 argmax agreement
   must be ≥ 99% or the export FAILS (FR-011, NFR-007)
6. frozen class order echoed into the report and ``build/labels.json``

Inputs are T8's artifacts: the checkpoint (``ultron train run`` →
``build/checkpoint.pt``) and the feature archive (``ultron features`` →
``build/features.npz``), loaded through ``ultron_model.train``'s own loaders.
The **frozen class order** is the checkpoint's ``classes`` (T8's
``derive_class_set``: ``wake``, ``noise`` + present commands in registry
order — FR-032/TD-012) and is checked against the archive's label set.

Module layout: everything up to ``render_parity_report`` is pure
NumPy/stdlib logic and stays importable (and testable) without the heavy
conversion stack; torch / onnx2tf / tensorflow are imported lazily inside the
functions that need them and report a clear error when missing
(``uv sync --extra export``).

Conversion notes (validated by the T9 smoke probe on a randomly initialized
model — the first checklist item):

* the global-average-pool must be expressed as ``mean(dim=-1)`` (ReduceMean);
  ``adaptive_avg_pool1d(z, 1).squeeze(-1)`` produces a Squeeze op that onnx2tf
  mis-transposes (channel-last). Mathematically identical — ``KwsNet`` uses
  the safe form.
* ``torch.onnx.export(..., dynamo=False)`` — the legacy exporter with
  ``opset_version=17``; torch ≥ 2.9 defaults to the dynamo path.
* the TFLite input signature comes back NWC ``(1, 100, 13)`` — exactly
  ``KwsNet``'s / the archive's layout, so features feed through unchanged.
* ``representative_dataset`` must yield a *list* of input arrays per sample.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from importlib import metadata, util
from pathlib import Path
from typing import Callable, Iterator, Sequence

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────────

#: Parity gate (FR-011 / NFR-007): PyTorch vs int8 argmax agreement.
PARITY_THRESHOLD = 0.99

#: NFR-002 (spec 003-can-act): int8 model flash budget on the XIAO.
INT8_FLASH_BUDGET_BYTES = 100 * 1024

#: Model input frames × MFCC coefficients (design.md §Model, T7 archive rows).
N_FRAMES = 100
N_MFCC = 13

_HEAVY_MODULES = ("torch", "onnx2tf", "tensorflow")


# ── Errors ───────────────────────────────────────────────────────────────────


class ExportError(RuntimeError):
    """Base class for export failures (clean CLI exit, no traceback)."""


class ExportDepsError(ExportError):
    """Heavy conversion dependencies are not installed."""


class ParityGateError(ExportError):
    """Parity agreement below the NFR-007 gate — the export FAILS."""


class LabelsOrderError(ExportError):
    """Class order mismatch between checkpoint, archive and labels.json."""


# ── Pure logic (NumPy only) ──────────────────────────────────────────────────


def check_labels_order(expected: Sequence[str], actual: Sequence[str], context: str) -> list[str]:
    """Order-sensitive equality check between two class orders.

    Raises :class:`LabelsOrderError` naming both orders on any difference
    (missing, extra or reordered labels all fail — the order is frozen).
    """
    expected_l = [str(x) for x in expected]
    actual_l = [str(x) for x in actual]
    if expected_l != actual_l:
        raise LabelsOrderError(
            f"class order mismatch ({context}): expected {expected_l}, got {actual_l} — "
            "the class order is frozen and must match everywhere "
            "(checkpoint ↔ feature archive ↔ build/labels.json)"
        )
    return actual_l


def check_archive_labels(archive_labels: Sequence[str], classes: Sequence[str]) -> None:
    """Archive label set must equal the checkpoint's frozen class set.

    Unknown archive labels would leave rows the model cannot classify, and a
    missing trained class means the representative dataset no longer covers
    the training distribution — both fail loudly (FR-032 re-freeze on retrain).
    """
    archive_set = {str(label) for label in archive_labels}
    class_set = {str(c) for c in classes}
    if archive_set != class_set:
        unknown = sorted(archive_set - class_set)
        missing = sorted(class_set - archive_set)
        details = []
        if unknown:
            details.append(f"archive labels not in the frozen class set: {unknown}")
        if missing:
            details.append(f"frozen classes absent from the archive: {missing}")
        raise LabelsOrderError(
            f"feature archive and checkpoint disagree on the class set — {'; '.join(details)}. "
            "The class order is frozen per training run (TD-012): retrain before exporting "
            "a changed class set."
        )


def write_labels_json(labels: Sequence[str], path: Path | str) -> Path:
    """Write the frozen class order to ``labels.json``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([str(x) for x in labels], indent=2) + "\n", encoding="utf-8")
    return path


def read_labels_json(path: Path | str) -> list[str]:
    """Read back ``labels.json`` (the build/labels.json order check)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise LabelsOrderError(f"{path}: expected a JSON array of label strings, got: {data!r}")
    return data


def argmax_agreement(reference: np.ndarray, other: np.ndarray) -> float:
    """Fraction of rows whose argmax class matches between two score matrices."""
    reference = np.asarray(reference)
    other = np.asarray(other)
    if reference.shape != other.shape:
        raise ExportError(f"score shape mismatch: {reference.shape} vs {other.shape}")
    if reference.ndim != 2 or reference.shape[0] == 0:
        raise ExportError(f"expected non-empty 2D score matrices, got {reference.shape}")
    return float((reference.argmax(axis=1) == other.argmax(axis=1)).mean())


def parity_gate(agreement: float, threshold: float = PARITY_THRESHOLD) -> None:
    """Enforce the NFR-007 gate — raise :class:`ParityGateError` below threshold."""
    if agreement < threshold:
        raise ParityGateError(
            f"parity gate FAILED: PyTorch vs int8 argmax agreement "
            f"{agreement:.2%} < {threshold:.2%} (FR-011 / NFR-007) — export rejected"
        )


def format_bytes(n: int) -> str:
    """Human-readable size in KB."""
    return f"{n / 1024:.1f} KB"


def budget_status(size: int, budget: int) -> str:
    """PASS / OVER BUDGET status of a size against a byte budget."""
    return "PASS" if size <= budget else "OVER BUDGET"


def sample_indices(n: int, cap: int) -> np.ndarray:
    """Deterministic evenly-strided sample indices (coverage-preserving cap)."""
    if n <= 0:
        raise ExportError("cannot sample from an empty feature set")
    if cap <= 0:
        raise ExportError("sample cap must be positive")
    return np.unique(np.linspace(0, n - 1, min(cap, n)).astype(np.int64))


def representative_dataset(
    features: np.ndarray, input_shape: tuple[int, ...], max_samples: int = 200
) -> Callable[[], Iterator[list[np.ndarray]]]:
    """Build a TFLite representative-dataset callable from training features.

    Returns a zero-argument callable yielding ``[array]`` lists (one input per
    sample, float32, shaped like the float model's input signature) — the form
    ``tf.lite``'s calibrator accepts. Deterministic (evenly strided).
    """
    indices = sample_indices(len(features), max_samples)

    def _generate() -> Iterator[list[np.ndarray]]:
        for i in indices:
            yield [np.asarray(features[i], dtype=np.float32).reshape(input_shape).copy()]

    return _generate


# ── Report rendering (pure) ──────────────────────────────────────────────────


@dataclass
class ExportResult:
    """Outcome of a successful or gate-failed export (artifacts are written)."""

    out_dir: Path
    labels: list[str]
    onnx_path: Path
    int8_path: Path
    fp16_path: Path
    labels_path: Path
    report_path: Path
    onnx_bytes: int
    int8_bytes: int
    fp16_bytes: int
    int8_agreement: float
    fp16_agreement: float
    n_parity: int
    threshold: float
    versions: dict[str, str] = field(default_factory=dict)

    @property
    def gate_passed(self) -> bool:
        return self.int8_agreement >= self.threshold

    @property
    def int8_within_budget(self) -> bool:
        return self.int8_bytes <= INT8_FLASH_BUDGET_BYTES


def render_parity_report(result: ExportResult) -> str:
    """Render ``parity_report.md`` (gate verdict, labels order, sizes, parity)."""
    verdict = "PASS" if result.gate_passed else "FAIL"
    int8_status = budget_status(result.int8_bytes, INT8_FLASH_BUDGET_BYTES)
    lines = [
        "# Parity report — ultron export tflite (T9)",
        "",
        f"- **Gate: {verdict}** — PyTorch vs int8 argmax agreement "
        f"{result.int8_agreement:.2%} (threshold ≥ {result.threshold:.2%}, FR-011 / NFR-007)",
        f"- Class order (frozen): {', '.join(result.labels)} — `build/labels.json` order check: OK",
        f"- Parity set: {result.n_parity} samples, evenly strided over the feature archive",
        "  (val-split wiring lands with T8's split hash)",
        "",
        "## Artifacts & sizes (vs NFR-002 budget, spec 003-can-act)",
        "",
        "| Artifact | Target | Size | Budget | Status |",
        "|---|---|---|---|---|",
        f"| `model_int8.tflite` | XIAO (flash) | {format_bytes(result.int8_bytes)} "
        f"| ≤ {format_bytes(INT8_FLASH_BUDGET_BYTES)} | {int8_status} |",
        f"| `model_fp16.tflite` | Orange Pi | {format_bytes(result.fp16_bytes)} | — | — |",
        f"| `model.onnx` | intermediate | {format_bytes(result.onnx_bytes)} | — | — |",
        "",
        "> NFR-002 confirmation on-device (TFLM arena ≤ 150 KB) is spec 003's T12/T13 —",
        "> the flash-budget status above is the export-side size report.",
        "",
        "## Parity (argmax agreement vs PyTorch)",
        "",
        "| Variant | Agreement | Samples | Threshold |",
        "|---|---|---|---|",
        f"| int8 full-quant (int8 in/out) | {result.int8_agreement:.2%} "
        f"| {result.n_parity} | ≥ {result.threshold:.2%} — {'PASS' if result.gate_passed else 'FAIL'} |",
        f"| float16 | {result.fp16_agreement:.2%} | {result.n_parity} | — |",
        "",
        "## Versions",
        "",
    ]
    lines += [f"- {name}: {version}" for name, version in result.versions.items()]
    return "\n".join(lines) + "\n"


# ── Torch-side helpers (lazy imports) ────────────────────────────────────────


def _require(module: str):
    """Import a heavy module or raise a clear ExportDepsError."""
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ExportDepsError(
            f"{module} is required for the T9 export chain but not installed ({exc}). "
            "Run `uv sync --extra export` — the end-to-end conversion requires a local run."
        ) from exc


def conversion_deps_available() -> bool:
    """True when the heavy conversion stack is importable (test wiring)."""
    return all(util.find_spec(m) is not None for m in _HEAVY_MODULES)


def dependency_versions() -> dict[str, str]:
    """Pinned versions for the report / NFR-006 metadata."""
    versions = {"python": sys.version.split()[0]}
    for dist in ("torch", "onnx", "onnx2tf", "tensorflow", "numpy"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:  # pragma: no cover - light env
            versions[dist] = "not installed"
    return versions


def reference_model(n_classes: int, seed: int | None = None, logit_scale: float = 1.0):
    """A randomly initialized ``KwsNet`` (design §Model, T8's architecture).

    Used by the T9 conversion smoke test on a randomly initialized model (the
    design's onnx2tf risk mitigation). ``logit_scale`` spreads the random
    logits so argmax survives int8 rounding in smoke settings (training makes
    real models confident anyway).
    """
    torch = _require("torch")
    from .model import KwsNet

    if seed is not None:
        torch.manual_seed(seed)
    model = KwsNet(num_classes=n_classes)
    if logit_scale != 1.0:
        with torch.no_grad():
            model.fc2.weight.mul_(logit_scale)
    model.eval()
    return model


def load_checkpoint(path: Path | str):
    """Load a T8 checkpoint → ``(eval-mode KwsNet, frozen class order)``.

    Delegates to ``ultron_model.train.load_checkpoint`` (format 1:
    ``model_state`` + ``classes`` …) and rebuilds the network.
    """
    from .model import KwsNet
    from .train import load_checkpoint as t8_load_checkpoint

    ckpt = t8_load_checkpoint(path)
    classes = [str(c) for c in ckpt["classes"]]
    model = KwsNet(num_classes=len(classes))
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, classes


# ── Conversion pipeline (lazy heavy imports) ─────────────────────────────────


def _predict_torch(model, features_nwc: np.ndarray) -> np.ndarray:
    """Run the model over NWC-archived features → (N, C) probabilities."""
    from .train import predict_probs

    return predict_probs(model, features_nwc)


def _run_tflite(model_bytes: bytes, features_nwc: np.ndarray) -> np.ndarray:
    """Run a TFLite flatbuffer over NWC-archived features → (N, C) float scores.

    Quantizes/dequantizes int8 in/out models with the model's own scales.
    """
    tf = _require("tensorflow")
    interpreter = tf.lite.Interpreter(model_content=model_bytes)
    interpreter.allocate_tensors()
    input_detail, output_detail = interpreter.get_input_details()[0], interpreter.get_output_details()[0]

    in_shape = tuple(input_detail["shape"])
    if in_shape[1:] == (N_FRAMES, N_MFCC):  # NWC — feed archive rows as-is
        feed = features_nwc
    elif in_shape[1:] == (N_MFCC, N_FRAMES):  # NCW
        feed = features_nwc.transpose(0, 2, 1)
    else:
        raise ExportError(f"unexpected TFLite input signature {in_shape}")

    quantized = np.issubdtype(input_detail["dtype"], np.signedinteger)
    in_scale, in_zp = input_detail["quantization"]
    out_scale, out_zp = output_detail["quantization"]

    scores = []
    for i in range(len(feed)):
        x = feed[i : i + 1].astype(np.float32)
        if quantized and in_scale:
            x = np.clip(np.round(x / in_scale) + in_zp, -128, 127).astype(np.int8)
        else:
            x = x.astype(input_detail["dtype"])
        interpreter.set_tensor(input_detail["index"], x)
        interpreter.invoke()
        y = interpreter.get_tensor(output_detail["index"]).astype(np.float32)
        if quantized and out_scale:
            y = (y - out_zp) * out_scale
        scores.append(y[0])
    return np.stack(scores)


def export_tflite(
    model,
    labels: Sequence[str],
    features: np.ndarray,
    out_dir: Path | str,
    *,
    threshold: float = PARITY_THRESHOLD,
    max_calibration: int = 200,
    max_parity: int = 1000,
    opset: int = 17,
) -> ExportResult:
    """Export a trained model to int8 + float16 TFLite with the parity gate.

    Writes ``model.onnx``, ``model_int8.tflite``, ``model_fp16.tflite``,
    ``labels.json`` and ``parity_report.md`` under ``out_dir``. Raises
    :class:`ParityGateError` when the PyTorch-vs-int8 argmax agreement is below
    ``threshold`` (default 99%, FR-011 / NFR-007) — artifacts and the report
    are still written so the failure is inspectable.

    ``model`` follows the ``KwsNet`` convention: ``(N, 100, 13)`` NWC in,
    ``(N, C)`` softmax probabilities out.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    features = np.asarray(features, dtype=np.float32)

    frozen = [str(x) for x in labels]
    if len(frozen) != len(set(frozen)) or not frozen:
        raise LabelsOrderError(f"invalid frozen class order: {frozen}")

    # model output width must match the frozen class order
    dummy = _predict_torch(model, np.zeros((1, N_FRAMES, N_MFCC), dtype=np.float32))
    n_classes = dummy.shape[1]
    if n_classes != len(frozen):
        raise ExportError(
            f"model has {n_classes} output classes but the frozen class order has "
            f"{len(frozen)} labels: {frozen}"
        )

    # 1. PyTorch → ONNX (legacy exporter, fixed batch 1 — deterministic MCU semantics).
    # KwsNet's runtime input-shape guard constant-folds under tracing — the
    # TracerWarning it emits is expected noise, not a tracing problem.
    import warnings

    torch = _require("torch")
    onnx_path = out_dir / "model.onnx"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=torch.jit.TracerWarning)
        torch.onnx.export(
            model,
            torch.zeros(1, N_FRAMES, N_MFCC),
            str(onnx_path),
            opset_version=opset,
            dynamo=False,
            input_names=["input"],
            output_names=["scores"],
        )

    # 2. ONNX → SavedModel via onnx2tf (SavedModel lands at the output-folder root)
    onnx2tf = _require("onnx2tf")
    _require("tensorflow")  # onnx2tf imports it lazily too; fail with our message first
    convert_dir = out_dir / "_onnx2tf"
    convert_dir.mkdir(exist_ok=True)
    onnx2tf.convert(
        input_onnx_file_path=str(onnx_path),
        output_folder_path=str(convert_dir),
        non_verbose=True,
    )
    saved_model_dir = next(
        (
            p
            for p in [convert_dir, *sorted(convert_dir.rglob("saved_model"))]
            if (p / "saved_model.pb").exists()
        ),
        None,
    )
    if saved_model_dir is None:
        raise ExportError(f"onnx2tf wrote no SavedModel under {convert_dir}")

    tf = _require("tensorflow")

    # float conversion first — introspects the real input signature (NWC)
    float_converter = tf.lite.TFLiteConverter.from_saved_model(str(saved_model_dir))
    float_bytes = float_converter.convert()
    float_input = tf.lite.Interpreter(model_content=float_bytes).get_input_details()[0]
    input_shape = tuple(int(d) for d in float_input["shape"])
    if input_shape[1:] not in ((N_FRAMES, N_MFCC), (N_MFCC, N_FRAMES)):
        raise ExportError(f"unexpected float TFLite input signature {input_shape}")

    # 3. full-int8 quantization with training features as representative data
    int8_converter = tf.lite.TFLiteConverter.from_saved_model(str(saved_model_dir))
    int8_converter.optimizations = [tf.lite.Optimize.DEFAULT]
    int8_converter.representative_dataset = representative_dataset(
        features, input_shape, max_samples=max_calibration
    )
    int8_converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    int8_converter.inference_input_type = tf.int8
    int8_converter.inference_output_type = tf.int8
    int8_bytes = int8_converter.convert()

    # 4. float16 variant
    fp16_converter = tf.lite.TFLiteConverter.from_saved_model(str(saved_model_dir))
    fp16_converter.optimizations = [tf.lite.Optimize.DEFAULT]
    fp16_converter.target_spec.supported_types = [np.float16]
    fp16_bytes = fp16_converter.convert()

    int8_path, fp16_path = out_dir / "model_int8.tflite", out_dir / "model_fp16.tflite"
    int8_path.write_bytes(int8_bytes)
    fp16_path.write_bytes(fp16_bytes)

    # 5. parity — PyTorch vs int8 on a deterministic sample of the archive
    parity_idx = sample_indices(len(features), max_parity)
    parity_features = features[parity_idx]
    torch_scores = _predict_torch(model, parity_features)
    int8_scores = _run_tflite(int8_bytes, parity_features)
    fp16_scores = _run_tflite(fp16_bytes, parity_features)
    int8_agreement = argmax_agreement(torch_scores, int8_scores)
    fp16_agreement = argmax_agreement(torch_scores, fp16_scores)

    # 6. frozen label order → labels.json (+ build/labels.json order check)
    labels_path = write_labels_json(frozen, out_dir / "labels.json")
    check_labels_order(frozen, read_labels_json(labels_path), "build/labels.json")

    result = ExportResult(
        out_dir=out_dir,
        labels=frozen,
        onnx_path=onnx_path,
        int8_path=int8_path,
        fp16_path=fp16_path,
        labels_path=labels_path,
        report_path=out_dir / "parity_report.md",
        onnx_bytes=onnx_path.stat().st_size,
        int8_bytes=len(int8_bytes),
        fp16_bytes=len(fp16_bytes),
        int8_agreement=int8_agreement,
        fp16_agreement=fp16_agreement,
        n_parity=len(parity_idx),
        threshold=threshold,
        versions=dependency_versions(),
    )
    result.report_path.write_text(render_parity_report(result), encoding="utf-8")

    # the gate raises AFTER everything is written — the failure stays inspectable
    parity_gate(int8_agreement, threshold)
    return result


def run_export(
    checkpoint: Path | str,
    features_path: Path | str,
    out_dir: Path | str,
    *,
    threshold: float = PARITY_THRESHOLD,
    max_calibration: int = 200,
    max_parity: int = 1000,
) -> ExportResult:
    """Orchestrator behind ``ultron export tflite`` (load → check → export)."""
    checkpoint, features_path, out_dir = Path(checkpoint), Path(features_path), Path(out_dir)
    for path, what in ((checkpoint, "checkpoint"), (features_path, "feature archive")):
        if not path.exists():
            raise ExportError(
                f"{what} not found: {path} — run `ultron train run` first (T8)"
            )

    from .train import load_archive

    model, classes = load_checkpoint(checkpoint)
    archive = load_archive(features_path)

    # labels.json order check, part 1: the archive's label set must equal the
    # checkpoint's frozen class set (part 2 re-reads build/labels.json)
    check_archive_labels(archive.labels, classes)

    return export_tflite(
        model,
        classes,
        archive.features,
        out_dir,
        threshold=threshold,
        max_calibration=max_calibration,
        max_parity=max_parity,
    )
