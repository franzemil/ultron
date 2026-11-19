"""``ultron infer`` — host inference on the exported TFLite artifacts (T10).

The exported flatbuffers are **executable and verifiable on the host** before
any device work (design.md §Host inference, TD-013):

``ultron infer files --model <tflite> --wav <file|folder>``
    Scores WAV clips with 2.0 s windows / 500 ms hop (TD-006) through the
    **shared C MFCC binding** (``dsp/mfcc.py`` — the same C bytes the devices
    run; no PyTorch, no NumPy MFCC shortcut), prints per-class scores and the
    decoded detection through ``model/thresholds.json`` (FR-034, Q-006). Batch
    mode over a *labeled* folder also emits a host-TFLite vs PyTorch argmax
    agreement report (FR-036).

``ultron infer mic --model <tflite>``
    Scores the host microphone continuously (2.0 s windows, 500 ms hop) and
    reports wake → command decisions within ``window_s`` (FR-035).

Runtime (TD-013): the exact exported flatbuffers are executed by
``tflite_runtime`` / LiteRT — int8 full-quant artifacts (int8 in **and** out)
are quantized/dequantized with the **artifact's own scales**, float16
artifacts are fed float32. TensorFlow stays build-time-only (TD-002): it is
only the last link of the interpreter fallback chain below.

Interpreter fallback chain (``tflite_runtime`` has no macOS arm64 wheels)::

    tflite_runtime  →  ai_edge_litert / litert (AI Edge LiteRT)  →  tf.lite

Missing every link fails loudly with the fix (``uv sync --extra export``
installs ``ai-edge-litert``).

Scope guard — **no actuation, no spec 003 event semantics**: ``infer mic``
only *reports* decisions (a wake followed by a command inside ``window_s``).
It never gates, latches or drives relays; the device state machine has exactly
one owner (spec 003-can-act, TD-013).
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, TextIO

import numpy as np
import typer

from .features import (
    HOP_LEN,
    NUM_COEFFS,
    SAMPLE_RATE,
    WINDOW_FRAMES,
    WINDOW_LEN,
    FeaturesError,
    Mfcc,
    _dc_correct,
    _load_wav,
    _window_features,
    _window_starts,
)

# ── Constants ────────────────────────────────────────────────────────────────

#: TFLite flatbuffer file identifier (bytes 4..8 of the artifact).
TFLITE_MAGIC = b"TFL3"

#: Class that has no operating point — the null / background class (TD-012).
NULL_CLASS = "noise"

DEFAULT_MODEL = Path("build/model_int8.tflite")
DEFAULT_THRESHOLDS = Path("model/thresholds.json")
DEFAULT_CHECKPOINT = Path("build/checkpoint.pt")
DEFAULT_AGREEMENT_REPORT = Path("build/infer_agreement.md")

#: FR-035 live-mic defaults.
DEFAULT_MIC_SECONDS = 30.0

#: FR-036 report caps (keep the markdown readable).
MAX_MISMATCH_ROWS = 10

#: Interpreter fallback chain, in the order TD-013 prescribes. ``ai-edge-litert``
#: ships both an ``ai_edge_litert`` and (in newer releases) a ``litert`` module.
_INTERPRETER_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("tflite_runtime.interpreter", "tflite_runtime"),
    ("ai_edge_litert.interpreter", "ai-edge-litert"),
    ("litert.interpreter", "litert (ai-edge-litert)"),
    ("tensorflow.lite", "tensorflow"),
)

_DEPS_HINT = (
    "install a TFLite interpreter — `uv sync --extra export` pulls in "
    "ai-edge-litert (works on macOS arm64, where tflite_runtime has no "
    "wheels); `pip install tflite-runtime` covers Linux/x86 hosts"
)


# ── Errors ───────────────────────────────────────────────────────────────────


class InferError(RuntimeError):
    """Base class for inference failures (clean CLI exit, no traceback)."""


class InferDepsError(InferError):
    """No TFLite interpreter (or PyTorch reference) is installed."""


class CorruptModelError(InferError):
    """The artifact is not a usable TFLite flatbuffer (FR-034 loud failure)."""


class ThresholdsError(InferError):
    """``model/thresholds.json`` is missing or violates the Q-006 schema."""


# ── TFLite interpreter (TD-013 fallback chain) ───────────────────────────────


def _default_loader(name: str):
    """Import ``name`` or return ``None`` (a missing link in the chain)."""
    try:
        return import_module(name)
    except ImportError:
        return None


def resolve_interpreter(loader: Optional[Callable[[str], object]] = None):
    """Return the first available TFLite ``Interpreter`` class.

    Chain (TD-013): ``tflite_runtime`` → AI Edge LiteRT (``litert``) →
    ``tf.lite``. ``loader`` is injectable so the fallback logic is testable
    without any of them installed. Raises :class:`InferDepsError` naming the
    fix when every link is missing.
    """
    load = loader or _default_loader
    tried: list[str] = []
    for module_name, dist in _INTERPRETER_CANDIDATES:
        module = load(module_name)
        if module is None:
            tried.append(f"{module_name} [{dist}] — not installed")
            continue
        interpreter = getattr(module, "Interpreter", None)
        if interpreter is None:
            tried.append(f"{module_name} [{dist}] — no Interpreter attribute")
            continue
        return interpreter
    raise InferDepsError(
        "no TFLite interpreter available (TD-013 fallback chain exhausted): "
        + "; ".join(tried)
        + f". {_DEPS_HINT}. This verify step REQUIRES A LOCAL RUN."
    )


def validate_model_file(path: Path | str) -> bytes:
    """Read an artifact and check the TFLite flatbuffer header (FR-034).

    A missing, empty, truncated or non-flatbuffer file fails loudly *before*
    any interpreter is constructed — so a deliberately corrupted artifact can
    never be silently scored.
    """
    path = Path(path)
    if not path.is_file():
        raise InferError(
            f"model not found: {path} — export it first (`ultron export tflite`, T9)"
        )
    data = path.read_bytes()
    if len(data) < 8:
        raise CorruptModelError(
            f"{path}: {len(data)} byte(s) — not a TFLite flatbuffer (empty or "
            "truncated); re-export with `ultron export tflite`"
        )
    if data[4:8] != TFLITE_MAGIC:
        raise CorruptModelError(
            f"{path}: not a TFLite flatbuffer — file identifier {data[4:8]!r} != "
            f"{TFLITE_MAGIC!r} (corrupt or wrong artifact); re-export with "
            "`ultron export tflite`"
        )
    return data


class TfliteRunner:
    """Runs one exported artifact over MFCC windows → (N, C) probabilities.

    Mirrors T9's ``_run_tflite`` (``model/src/ultron_model/export.py``) so the
    host path sees exactly what the parity gate saw: int8 in/out artifacts are
    quantized into the model's own input scale/zero-point and dequantized out
    of its own output scale/zero-point; float16/float32 artifacts take float32
    directly.
    """

    def __init__(
        self,
        model_path: Path | str = DEFAULT_MODEL,
        interpreter_cls=None,
    ):
        self.path = Path(model_path)
        self._bytes = validate_model_file(self.path)
        self._interpreter_cls = interpreter_cls or resolve_interpreter()
        try:
            self._interpreter = self._interpreter_cls(model_content=self._bytes)
            self._interpreter.allocate_tensors()
            self._input = self._interpreter.get_input_details()[0]
            self._output = self._interpreter.get_output_details()[0]
        except InferError:
            raise
        except Exception as exc:  # noqa: BLE001 — any parse/allocate failure is loud
            raise CorruptModelError(
                f"{self.path}: TFLite interpreter cannot load this artifact ({exc}) "
                "— the flatbuffer is corrupt or was written by an incompatible "
                "converter; re-export with `ultron export tflite`"
            ) from exc

        in_shape = tuple(int(d) for d in self._input["shape"])
        if in_shape[1:] == (WINDOW_FRAMES, NUM_COEFFS):
            self.layout = "NWC"
        elif in_shape[1:] == (NUM_COEFFS, WINDOW_FRAMES):
            self.layout = "NCW"
        else:
            raise InferError(
                f"{self.path}: unexpected input signature {in_shape} — expected "
                f"(1, {WINDOW_FRAMES}, {NUM_COEFFS}) from the T9 export"
            )
        self.input_shape = in_shape

        out_shape = tuple(int(d) for d in self._output["shape"])
        self.n_classes = out_shape[-1]
        self._in_dtype = self._input["dtype"]
        self._out_dtype = self._output["dtype"]
        self._in_scale, self._in_zero_point = _quant_params(self._input)
        self._out_scale, self._out_zero_point = _quant_params(self._output)
        self.quantized = np.issubdtype(self._in_dtype, np.signedinteger) and bool(
            self._in_scale
        )

    # -- description ---------------------------------------------------------

    @property
    def backend(self) -> str:
        """Interpreter backend actually used (reported for NFR-006 traceability)."""
        module = getattr(self._interpreter_cls, "__module__", "") or ""
        return module.split(".")[0] or type(self._interpreter_cls).__name__

    @property
    def descriptor(self) -> str:
        """One-line artifact description: name, quantization, signature, classes."""
        quant = (
            f"int8 in/out (input scale {self._in_scale:.5f}, zero-point "
            f"{self._in_zero_point}; output scale {self._out_scale:.5f})"
            if self.quantized
            else f"{np.dtype(self._in_dtype).name} in / "
            f"{np.dtype(self._out_dtype).name} out"
        )
        return (
            f"{self.path.name} · {quant} · {self.layout} {self.input_shape} · "
            f"{self.n_classes} classes · {self.backend}"
        )

    # -- inference -----------------------------------------------------------

    def scores(self, features_nwc: np.ndarray) -> np.ndarray:
        """Run (N, 100, 13) float32 MFCC windows → (N, C) float32 probabilities."""
        features = np.asarray(features_nwc, dtype=np.float32)
        if features.ndim != 3 or features.shape[1:] != (WINDOW_FRAMES, NUM_COEFFS):
            raise InferError(
                f"expected MFCC windows ({WINDOW_FRAMES}, {NUM_COEFFS}) per row, "
                f"got {features.shape}"
            )
        feed = features if self.layout == "NWC" else features.transpose(0, 2, 1)
        out = np.empty((len(feed), self.n_classes), dtype=np.float32)
        for i in range(len(feed)):
            self._interpreter.set_tensor(
                self._input["index"], self._quantize(feed[i : i + 1])
            )
            try:
                self._interpreter.invoke()
            except Exception as exc:  # noqa: BLE001
                raise CorruptModelError(
                    f"{self.path}: interpreter.invoke() failed ({exc}) — the "
                    "artifact is corrupt or unsupported by this runtime"
                ) from exc
            raw = np.asarray(
                self._interpreter.get_tensor(self._output["index"])
            ).reshape(-1)
            out[i] = self._dequantize(raw)
        return out

    def _quantize(self, x: np.ndarray) -> np.ndarray:
        """float32 → the input tensor's dtype, using the artifact's own scale."""
        if self.quantized:
            info = np.iinfo(self._in_dtype)
            scaled = np.round(x / self._in_scale) + self._in_zero_point
            return np.clip(scaled, info.min, info.max).astype(self._in_dtype)
        return x.astype(self._in_dtype)

    def _dequantize(self, raw: np.ndarray) -> np.ndarray:
        """The output tensor's dtype → float32 scores, using its own scale."""
        values = raw.astype(np.float32)
        if np.issubdtype(self._out_dtype, np.signedinteger) and self._out_scale:
            return (values - self._out_zero_point) * self._out_scale
        return values


def _quant_params(detail: dict) -> tuple[float, int]:
    """(scale, zero-point) of a tensor detail; (0.0, 0) for float tensors."""
    quant = detail.get("quantization")
    if quant is None:
        return 0.0, 0
    try:
        scale, zero_point = quant[0], quant[1]
    except (IndexError, TypeError):
        return 0.0, 0
    scales = np.atleast_1d(np.asarray(scale, dtype=np.float64))
    zeros = np.atleast_1d(np.asarray(zero_point, dtype=np.int64))
    if scales.size == 0 or not float(scales.flat[0]):
        return 0.0, 0
    return float(scales.flat[0]), int(zeros.flat[0]) if zeros.size else 0


# ── Class order (frozen per export) ──────────────────────────────────────────


def resolve_labels(
    labels_path: Path | str | None, model_path: Path | str = DEFAULT_MODEL
) -> list[str]:
    """Frozen class order for the artifact — ``build/labels.json`` (T9).

    Defaults to ``labels.json`` next to the model (``build/model_int8.tflite``
    → ``build/labels.json``). The order is frozen per export, so a missing or
    misaligned file is a loud failure: scoring with the wrong order would
    silently mislabel every detection.
    """
    path = Path(labels_path) if labels_path is not None else Path(model_path).parent / "labels.json"
    if not path.is_file():
        raise InferError(
            f"class order not found: {path} — `ultron export tflite` (T9) writes "
            "build/labels.json next to the artifacts; pass --labels to override"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise InferError(f"{path}: cannot parse class order ({exc})") from exc
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) for x in raw):
        raise InferError(
            f"{path}: expected a non-empty JSON array of label strings (T9 "
            f"labels.json), got {raw!r}"
        )
    return [str(x) for x in raw]


def check_class_alignment(labels: Sequence[str], n_classes: int, context: str) -> None:
    """Artifact output width must match the frozen class order (FR-032 re-freeze)."""
    if len(labels) != n_classes:
        raise InferError(
            f"{context}: class order has {len(labels)} label(s) "
            f"[{', '.join(labels)}] but the artifact emits {n_classes} classes — "
            "the artifact and build/labels.json are out of sync (retrain + "
            "re-export, FR-032)"
        )


# ── Thresholds (Q-006) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class OperatingPoint:
    """Per-class runtime rule: accept only if score ≥ threshold AND lead ≥ margin."""

    threshold: float
    margin: float


@dataclass(frozen=True)
class Thresholds:
    """``model/thresholds.json`` — the Q-006 operating point document."""

    wake: float
    margin_wake: float
    commands: dict[str, OperatingPoint]
    window_s: float
    path: Optional[Path] = None

    def operating_point(self, label: str) -> Optional[OperatingPoint]:
        """Rule for a class label — ``None`` when the class has no operating point."""
        if label == "wake":
            return OperatingPoint(self.wake, self.margin_wake)
        return self.commands.get(label)


def _probability(value: object, context: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ThresholdsError(f"{context}: expected a number, got {value!r}") from exc
    if not 0.0 <= number <= 1.0:
        raise ThresholdsError(f"{context}: expected a probability in [0, 1], got {number}")
    return number


def _positive_seconds(value: object, context: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ThresholdsError(f"{context}: expected a number, got {value!r}") from exc
    if number <= 0:
        raise ThresholdsError(f"{context}: expected seconds > 0, got {number}")
    return number


def load_thresholds(path: Path | str = DEFAULT_THRESHOLDS) -> Thresholds:
    """Load + validate ``model/thresholds.json`` (Q-006 schema, FR-015).

    Schema::

        {"wake": 0.80, "margin_wake": 0.10,
         "commands": {"<id>": {"threshold": 0.80, "margin": 0.10}},
         "window_s": 5.0}

    Every violation fails loudly — a silent default would change the operating
    point without a PR-documented threshold change (Q-006).
    """
    path = Path(path)
    if not path.is_file():
        raise ThresholdsError(
            f"thresholds not found: {path} — run `ultron train sweep` (T8, Q-006) "
            "or pass --thresholds"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ThresholdsError(f"{path}: cannot parse thresholds JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise ThresholdsError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    missing = {"wake", "margin_wake", "commands", "window_s"} - set(raw)
    if missing:
        raise ThresholdsError(
            f"{path}: missing key(s) {sorted(missing)} — expected the Q-006 schema "
            "{'wake', 'margin_wake', 'commands', 'window_s'}"
        )
    commands_raw = raw["commands"]
    if not isinstance(commands_raw, dict):
        raise ThresholdsError(
            f"{path}: 'commands' must be an object of id → {{threshold, margin}}, "
            f"got {type(commands_raw).__name__}"
        )
    commands: dict[str, OperatingPoint] = {}
    for label, entry in commands_raw.items():
        if not isinstance(entry, dict) or {"threshold", "margin"} - set(entry):
            raise ThresholdsError(
                f"{path}: commands['{label}'] must be {{'threshold': …, 'margin': …}}, "
                f"got {entry!r}"
            )
        commands[str(label)] = OperatingPoint(
            _probability(entry["threshold"], f"{path}: commands['{label}'].threshold"),
            _probability(entry["margin"], f"{path}: commands['{label}'].margin"),
        )
    window_s = _positive_seconds(raw["window_s"], f"{path}: window_s")
    return Thresholds(
        wake=_probability(raw["wake"], f"{path}: wake"),
        margin_wake=_probability(raw["margin_wake"], f"{path}: margin_wake"),
        commands=commands,
        window_s=window_s,
        path=path,
    )


# ── Decoding (FR-015 runtime rule) ───────────────────────────────────────────


@dataclass(frozen=True)
class Detection:
    """One decoded analysis window."""

    argmax_label: str
    score: float
    runner_up: float
    operating_point: Optional[OperatingPoint]
    accepted: bool
    reason: str

    @property
    def label(self) -> Optional[str]:
        """The detected class label, or ``None`` when nothing is accepted."""
        return self.argmax_label if self.accepted else None

    @property
    def lead(self) -> float:
        """Top-1 minus runner-up probability."""
        return self.score - self.runner_up

    def verdict(self) -> str:
        return self.argmax_label if self.accepted else "—"


def decode_window(
    scores: np.ndarray, labels: Sequence[str], thresholds: Thresholds
) -> Detection:
    """Decode one window's class scores with the per-class operating point.

    Runtime rule (FR-015, identical to T8's ``decode_with_thresholds``, whose
    sweep tuned these values): the argmax class is accepted only if its
    probability ≥ **that class's** threshold AND it leads the runner-up by ≥
    **that class's** margin. ``noise`` is the null class — no operating point,
    never a detection.
    """
    row = np.asarray(scores, dtype=np.float32).reshape(-1)
    if len(row) != len(labels):
        raise InferError(
            f"score vector has {len(row)} entries but the class order has "
            f"{len(labels)} labels ({', '.join(labels)})"
        )
    if len(row) == 1:
        raise InferError("a single-class artifact cannot decode a detection")
    order = np.argsort(row)[::-1]
    top, second = int(order[0]), int(order[1])
    argmax_label = str(labels[top])
    score = float(row[top])
    runner_up = float(row[second])
    lead = score - runner_up

    point = thresholds.operating_point(argmax_label)
    if point is None:
        why = (
            f"rejected: 'noise' is the null class (no operating point)"
            if argmax_label == NULL_CLASS
            else f"rejected: no operating point for '{argmax_label}' in "
            f"{thresholds.path or 'thresholds.json'} (trained commands only, FR-032)"
        )
        return Detection(argmax_label, score, runner_up, None, False, why)

    if score < point.threshold:
        return Detection(
            argmax_label,
            score,
            runner_up,
            point,
            False,
            f"rejected: score {score:.3f} < threshold {point.threshold:.2f}",
        )
    if lead < point.margin:
        return Detection(
            argmax_label,
            score,
            runner_up,
            point,
            False,
            f"rejected: lead {lead:.3f} < margin {point.margin:.2f}",
        )
    return Detection(
        argmax_label,
        score,
        runner_up,
        point,
        True,
        f"accepted: score {score:.3f} ≥ {point.threshold:.2f}, "
        f"lead {lead:.3f} ≥ {point.margin:.2f}",
    )


def decode_scores(
    scores: np.ndarray, labels: Sequence[str], thresholds: Thresholds
) -> list[Detection]:
    """Decode an (N, C) score matrix window by window."""
    matrix = np.asarray(scores, dtype=np.float32)
    if matrix.ndim != 2:
        raise InferError(f"expected a 2D score matrix, got {matrix.shape}")
    return [decode_window(row, labels, thresholds) for row in matrix]


def format_scores(detection: Detection, scores: np.ndarray, labels: Sequence[str]) -> str:
    """``wake 0.021  noise 0.014  lb 0.902  la 0.063`` — per-class scores in class order."""
    row = np.asarray(scores, dtype=np.float32).reshape(-1)
    return "  ".join(f"{label} {float(row[i]):.4f}" for i, label in enumerate(labels))


# ── Clip scoring (FR-034) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class WindowResult:
    """One analysis window of one clip."""

    start_s: float
    scores: np.ndarray
    detection: Detection


@dataclass
class ClipResult:
    """All windows of one scored clip."""

    path: Path
    expected: Optional[str]
    windows: list[WindowResult] = field(default_factory=list)
    #: (W, 100, 13) MFCC windows — the PyTorch reference input (FR-036).
    feature_rows: Optional[np.ndarray] = None

    @property
    def detections(self) -> list[Detection]:
        return [w.detection for w in self.windows if w.detection.accepted]

    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for detection in self.detections:
            label = detection.label or ""
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items()))


def open_mfcc() -> Mfcc:
    """The shared C MFCC binding (dsp/mfcc.py) — never a NumPy MFCC shortcut."""
    try:
        return Mfcc(SAMPLE_RATE)
    except Exception as exc:  # noqa: BLE001 — build/load failures are loud
        raise InferError(
            f"cannot load the shared C MFCC library (dsp/build/libmfcc.so): {exc} — "
            "build it with `make -C dsp` (required on a fresh checkout)"
        ) from exc


def window_features(samples: np.ndarray, mfcc: Mfcc) -> tuple[np.ndarray, list[int]]:
    """(W, 100, 13) MFCC windows + their start samples — TD-006, T7-identical.

    Same windowing the feature archive uses (``ultron features``): 2.0 s
    windows hopped by 500 ms, short clips zero-padded to one full window.
    """
    samples = np.asarray(samples)
    starts = _window_starts(len(samples))
    rows = np.empty((len(starts), WINDOW_FRAMES, NUM_COEFFS), dtype=np.float32)
    for i, start in enumerate(starts):
        if len(samples) < WINDOW_LEN:
            window = np.zeros(WINDOW_LEN, dtype=np.int16)
            window[: len(samples)] = samples
        else:
            window = samples[start : start + WINDOW_LEN]
        rows[i] = _window_features(window, mfcc)
    if np.isnan(rows).any():
        raise InferError(
            "NaN MFCC row(s) — refusing to score corrupt audio (FR-022, spec 001)"
        )
    return rows, starts


def load_clip_samples(path: Path | str) -> np.ndarray:
    """Read a clip through the T7 loader (16 kHz/16-bit/mono, DC-corrected)."""
    path = Path(path)
    try:
        return _dc_correct(_load_wav(path, str(path)))
    except FeaturesError as exc:
        raise InferError(str(exc)) from exc


def score_clip(
    path: Path | str,
    runner: TfliteRunner,
    mfcc: Mfcc,
    thresholds: Thresholds,
    labels: Sequence[str],
    expected: Optional[str] = None,
) -> ClipResult:
    """Score one WAV through the artifact → per-window scores + detections."""
    path = Path(path)
    features, starts = window_features(load_clip_samples(path), mfcc)
    scores = runner.scores(features)
    check_class_alignment(labels, scores.shape[1], f"{path}")
    result = ClipResult(path=path, expected=expected, feature_rows=features)
    for start, row in zip(starts, scores):
        result.windows.append(
            WindowResult(
                start_s=start / SAMPLE_RATE,
                scores=row,
                detection=decode_window(row, labels, thresholds),
            )
        )
    return result


def collect_wavs(target: Path | str) -> tuple[list[Path], bool]:
    """Resolve ``--wav`` to ``(sorted wav paths, was_a_directory)``."""
    target = Path(target)
    if target.is_dir():
        paths = sorted(p for p in target.rglob("*.wav") if p.is_file())
        if not paths:
            raise InferError(f"{target}: no .wav files found (pass a file or a folder)")
        return paths, True
    if target.is_file():
        if target.suffix.lower() != ".wav":
            raise InferError(f"{target}: not a .wav file (only 16 kHz/16-bit/mono WAVs)")
        return [target], False
    raise InferError(f"{target}: not found — pass a WAV file or a folder")


def expected_label(path: Path, labels: Sequence[str]) -> Optional[str]:
    """The ``<label>/clip.wav`` folder name when it is a known class, else None."""
    parent = Path(path).parent.name
    return parent if parent in set(labels) else None


def is_labeled(paths: Iterable[Path], labels: Sequence[str]) -> bool:
    """True when every clip sits in a known ``<label>/`` directory (FR-036)."""
    paths = list(paths)
    return bool(paths) and all(expected_label(p, labels) is not None for p in paths)


# ── FR-036: host-TFLite vs PyTorch agreement report ─────────────────────────


def torch_reference_scores(
    features: np.ndarray, checkpoint: Path | str = DEFAULT_CHECKPOINT
) -> tuple[np.ndarray, list[str]]:
    """(N, C) PyTorch probabilities + frozen classes — the FR-036 reference.

    Torch is imported lazily (heavy) and only when an agreement report is
    actually requested.
    """
    checkpoint = Path(checkpoint)
    if not checkpoint.is_file():
        raise InferError(
            f"checkpoint not found: {checkpoint} — the FR-036 agreement report "
            "needs the PyTorch reference; run `ultron train run` (T8), pass "
            "--checkpoint, or skip the report with --no-agreement"
        )
    try:
        from ultron_model.export import load_checkpoint
        from ultron_model.train import predict_probs
    except ImportError as exc:  # pragma: no cover - torch is a core dependency
        raise InferDepsError(
            f"the PyTorch reference is unavailable ({exc}) — torch is a core "
            "dependency of ultron-model; run `uv sync`"
        ) from exc
    try:
        model, classes = load_checkpoint(checkpoint)
        return np.asarray(predict_probs(model, np.asarray(features, np.float32))), classes
    except Exception as exc:  # noqa: BLE001 — unreadable checkpoints fail loudly
        raise InferError(f"{checkpoint}: cannot score with PyTorch ({exc})") from exc


def agreement_stats(
    tflite_argmax: np.ndarray,
    torch_argmax: np.ndarray,
    labels: Sequence[str],
    expected_idx: Optional[np.ndarray] = None,
) -> dict:
    """Overall + per-class argmax agreement between host TFLite and PyTorch.

    ``expected_idx`` (optional) adds tflite-vs-expected accuracy so a labeled
    folder reports both parity and correctness.
    """
    tflite_argmax = np.asarray(tflite_argmax, dtype=np.int64)
    torch_argmax = np.asarray(torch_argmax, dtype=np.int64)
    if tflite_argmax.shape != torch_argmax.shape:
        raise InferError(
            f"agreement needs equal-length argmax vectors, got "
            f"{tflite_argmax.shape} vs {torch_argmax.shape}"
        )
    if tflite_argmax.size == 0:
        raise InferError("cannot compute agreement over zero windows")
    match = tflite_argmax == torch_argmax
    per_label: dict[str, dict] = {}
    for index, label in enumerate(labels):
        mask = tflite_argmax == index
        n = int(mask.sum())
        if not n:
            continue
        entry = {
            "windows": n,
            "agreement": float(match[mask].mean()),
        }
        if expected_idx is not None:
            entry["expected"] = int(np.sum(np.asarray(expected_idx)[mask] == index))
        per_label[label] = entry
    stats = {
        "n_windows": int(tflite_argmax.size),
        "n_mismatch": int((~match).sum()),
        "agreement": float(match.mean()),
        "per_label": per_label,
        "mismatch_rows": np.flatnonzero(~match).tolist(),
    }
    if expected_idx is not None:
        stats["accuracy"] = float((tflite_argmax == np.asarray(expected_idx)).mean())
    return stats


def render_agreement_report(
    *,
    artifact: Path | str,
    descriptor: str,
    checkpoint: Path | str,
    labels: Sequence[str],
    threshold_path: Optional[Path],
    stats: dict,
    rows: Sequence[dict],
) -> str:
    """Render the FR-036 markdown report (agreement, per-class, mismatches)."""
    verdict = "PASS" if stats["agreement"] >= 0.99 else "BELOW NFR-007 GATE"
    lines = [
        "# Host inference agreement report — `ultron infer files` (T10)",
        "",
        f"- Artifact: `{Path(artifact)}` — {descriptor}",
        f"- PyTorch reference: `{Path(checkpoint)}`",
        f"- Class order (frozen, `build/labels.json`): {', '.join(labels)}",
        f"- Thresholds: `{threshold_path}`" if threshold_path else "- Thresholds: —",
    ]
    if "accuracy" in stats:
        lines.append(
            f"- Labeled windows: {stats.get('n_labeled', 0)} "
            f"(host-TFLite accuracy vs folder label: {stats['accuracy']:.2%})"
        )
    lines += [
        "",
        f"- **host-TFLite vs PyTorch argmax agreement: {stats['agreement']:.2%}** over "
        f"{stats['n_windows']} windows ({stats['n_mismatch']} mismatch) — {verdict}",
        "",
        "> On-demand, per-folder complement of the NFR-007 export gate"
        " (`build/parity_report.md`): same rule, any artifact, any folder."
        " Disagreement means the host executes a different classifier than the"
        " trained one.",
        "",
        "## Per-class (argmax of the TFLite artifact)",
        "",
        "| Class | Windows | Agreement |",
        "|---|---|---|",
    ]
    for label, entry in stats["per_label"].items():
        lines.append(f"| {label} | {entry['windows']} | {entry['agreement']:.2%} |")
    lines += [
        "",
        f"## Mismatches (first {MAX_MISMATCH_ROWS})",
        "",
    ]
    if not rows:
        lines.append("None — host TFLite and PyTorch agree on every window.")
    else:
        lines += [
            "| Clip | Window | TFLite argmax | PyTorch argmax | Expected |",
            "|---|---|---|---|---|",
        ]
        for row in rows[:MAX_MISMATCH_ROWS]:
            lines.append(
                f"| `{row['clip']}` | {row['start_s']:.2f}s | "
                f"{row['tflite']} ({row['tflite_score']:.3f}) | "
                f"{row['torch']} ({row['torch_score']:.3f}) | {row['expected'] or '—'} |"
            )
        if len(rows) > MAX_MISMATCH_ROWS:
            lines.append(f"\n({len(rows) - MAX_MISMATCH_ROWS} more not shown)")
    lines += ["", "## Versions", ""]
    lines += [f"- {name}: {version}" for name, version in stats.get("versions", {}).items()]
    return "\n".join(line for line in lines if line is not None) + "\n"


def build_agreement(
    results: Sequence[ClipResult],
    labels: Sequence[str],
    checkpoint: Path | str,
    *,
    artifact: Path | str,
    descriptor: str,
    threshold_path: Optional[Path] = None,
    backend: str = "",
) -> tuple[str, dict]:
    """Compute the FR-036 agreement for scored clips; returns (markdown, stats)."""
    if not results:
        raise InferError("no clips scored — nothing to compare")
    missing = [r.path for r in results if r.feature_rows is None]
    if missing:
        raise InferError(
            f"{missing[0]}: no MFCC rows recorded — cannot build the PyTorch reference"
        )
    features = np.concatenate([r.feature_rows for r in results])
    tflite_scores = np.concatenate([np.stack([w.scores for w in r.windows]) for r in results])
    torch_scores, torch_classes = torch_reference_scores(features, checkpoint)
    check_class_alignment(labels, torch_scores.shape[1], f"{checkpoint} vs labels.json")
    torch_order = [str(c) for c in torch_classes]
    if torch_order != list(labels):
        raise InferError(
            f"class order mismatch (checkpoint vs build/labels.json): expected "
            f"{list(labels)}, got {torch_order} — the class order is frozen per "
            "training run (FR-032); retrain + re-export"
        )

    flat: list[tuple[ClipResult, WindowResult]] = [
        (clip, window) for clip in results for window in clip.windows
    ]
    expected_idx = np.array(
        [labels.index(clip.expected) if clip.expected in labels else -1 for clip, _ in flat],
        dtype=np.int64,
    )
    known = expected_idx >= 0
    tflite_argmax = tflite_scores.argmax(axis=1)
    torch_argmax = torch_scores.argmax(axis=1)
    stats = agreement_stats(
        tflite_argmax,
        torch_argmax,
        labels,
        expected_idx=expected_idx if known.any() else None,
    )
    stats["n_labeled"] = int(known.sum())
    stats["versions"] = _report_versions(backend)

    rows = []
    for i in stats["mismatch_rows"]:
        clip, window = flat[i]
        rows.append(
            {
                "clip": clip.path.as_posix(),
                "start_s": window.start_s,
                "tflite": labels[int(tflite_argmax[i])],
                "tflite_score": float(tflite_scores[i][tflite_argmax[i]]),
                "torch": labels[int(torch_argmax[i])],
                "torch_score": float(torch_scores[i][torch_argmax[i]]),
                "expected": clip.expected,
            }
        )
    markdown = render_agreement_report(
        artifact=artifact,
        descriptor=descriptor,
        checkpoint=checkpoint,
        labels=labels,
        threshold_path=threshold_path,
        stats=stats,
        rows=rows,
    )
    return markdown, stats


def _report_versions(backend: str) -> dict[str, str]:
    """Versions for the report (NFR-006 traceability) — stdlib only, no imports."""
    import platform
    import sys

    versions = {"python": sys.version.split()[0], "numpy": np.__version__}
    if backend:
        versions["interpreter"] = backend
    versions["platform"] = platform.platform(terse=True)
    return versions


def write_report(markdown: str, path: Path | str) -> Path:
    """Write the agreement report (mkdir -p) and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path


# ── FR-035: continuous host-mic decisions (reporting only) ──────────────────


def format_clock(t_s: float) -> str:
    """``mm:ss.s`` clock for the live stream (minutes unbounded past 59)."""
    minutes, seconds = divmod(max(float(t_s), 0.0), 60.0)
    return f"{int(minutes):02d}:{seconds:04.1f}"


def format_live_line(t_s: float, detection: Detection) -> str:
    """One console line for one scored window — what the model actually said.

    Prints the decoded **argmax label and its score**, i.e. exactly what
    ``decode_window`` saw for that window (the same argmax/threshold path) —
    never a prettified guess. A window that clears its operating point is
    marked ``✓``; a window that does not still prints its top label and score
    (that is the ``noise noise …`` part of the stream).
    """
    marker = " ✓" if detection.accepted else ""
    return f"[{format_clock(t_s)}] {detection.argmax_label} {detection.score:.3f}{marker}"


def flush_print(line: str, stream: Optional[TextIO] = None) -> None:
    """Write one line and flush it — a live stream must never sit in a buffer.

    When piped, stdout is block-buffered; flushing per line keeps the per-window
    stream real-time instead of dumping it in bursts.
    """
    out = stream if stream is not None else sys.stdout
    out.write(line + "\n")
    out.flush()


@dataclass(frozen=True)
class MicDecision:
    """One accepted detection in the live stream, with its wake association."""

    t_s: float
    detection: Detection
    wake_t_s: Optional[float] = None

    @property
    def label(self) -> str:
        return self.detection.label or ""

    @property
    def after_wake_s(self) -> Optional[float]:
        """Seconds after the associated wake, if any."""
        return None if self.wake_t_s is None else self.t_s - self.wake_t_s

    @property
    def within_window(self) -> bool:
        return self.after_wake_s is not None

    def line(self, window_s: float) -> str:
        """Decision line — visually distinct from the per-window label stream."""
        stamp = format_clock(self.t_s)
        score = self.detection.score
        if self.label == "wake":
            return f"[{stamp}] DETECTED wake (score {score:.3f})"
        if self.wake_t_s is None:
            return (
                f"[{stamp}] DETECTED {self.label} (score {score:.3f}) — "
                f"no wake within the last {window_s:.1f}s"
            )
        return (
            f"[{stamp}] DETECTED wake → {self.label} (score {score:.3f}, "
            f"{self.after_wake_s:.2f}s after wake, window {window_s:.1f}s)"
        )


class DecisionReporter:
    """Reporting-only wake → command association for ``infer mic`` (FR-035).

    **Not** a state machine: it never gates audio, latches a mode or drives a
    relay. It records accepted detections and, for a command, the most recent
    wake inside ``window_s`` — a *report*, not spec 003 event semantics (the
    device state machine has exactly one owner).
    """

    def __init__(self, window_s: float):
        if window_s <= 0:
            raise InferError(f"window_s must be > 0, got {window_s}")
        self.window_s = window_s
        self.events: list[MicDecision] = []
        self._last_wake_t: Optional[float] = None

    def add(self, t_s: float, detection: Detection) -> Optional[MicDecision]:
        """Record one accepted detection (rejected windows are ignored)."""
        if not detection.accepted or detection.label is None:
            return None
        wake_t: Optional[float] = None
        if detection.label == "wake":
            self._last_wake_t = t_s
        elif self._last_wake_t is not None and t_s - self._last_wake_t <= self.window_s:
            wake_t = self._last_wake_t
        elif self._last_wake_t is not None:
            self._last_wake_t = None  # stale: outside the listening window
        decision = MicDecision(t_s=t_s, detection=detection, wake_t_s=wake_t)
        self.events.append(decision)
        return decision

    def counts(self) -> dict[str, int]:
        wakes = sum(1 for e in self.events if e.label == "wake")
        paired = sum(1 for e in self.events if e.label != "wake" and e.within_window)
        unpaired = sum(1 for e in self.events if e.label != "wake" and not e.within_window)
        return {"wake": wakes, "commands_after_wake": paired, "commands_alone": unpaired}


def run_mic_session(
    blocks: Iterable[np.ndarray],
    runner: TfliteRunner,
    mfcc: Mfcc,
    thresholds: Thresholds,
    labels: Sequence[str],
    *,
    log: Callable[[str], None] = flush_print,
    stream_windows: bool = True,
) -> DecisionReporter:
    """Score a live block stream: 2.0 s windows, 500 ms hop, decide + report.

    ``blocks`` yields int16 mono samples at 16 kHz (any block size). Every
    window is scored through the artifact and decoded with
    ``model/thresholds.json``. With ``stream_windows`` (the default) **every**
    window emits exactly one line through ``log`` the moment it is scored —
    timestamp + decoded top label + score — so the owner can watch the model
    think (``noise noise wake wake la la``); accepted detections additionally
    emit a visually distinct decision line with their wake association
    (FR-035). ``stream_windows=False`` keeps only the decision lines.

    Pure reporting — no actuation, no spec 003 event semantics (TD-013).
    """
    reporter = DecisionReporter(thresholds.window_s)
    buffer = np.zeros(0, dtype=np.int16)
    buffer_start = 0  # absolute sample index of buffer[0]
    total = 0  # absolute samples received
    window_end = WINDOW_LEN  # absolute sample index of the next window's end
    scored = 0
    for block in blocks:
        chunk = np.asarray(block, dtype=np.int16).reshape(-1)
        if chunk.size == 0:
            continue
        buffer = np.concatenate([buffer, chunk])
        total += chunk.size
        while window_end <= total:
            lo = window_end - WINDOW_LEN - buffer_start
            hi = window_end - buffer_start
            window = buffer[lo:hi]
            if window.size == WINDOW_LEN:
                samples = _dc_correct(window.astype(np.int32))
                features, _starts = window_features(samples, mfcc)
                scores = runner.scores(features)[0]
                detection = decode_window(scores, labels, thresholds)
                scored += 1
                t_s = window_end / SAMPLE_RATE
                if stream_windows:
                    log(format_live_line(t_s, detection))
                decision = reporter.add(t_s, detection)
                if decision is not None:
                    log(decision.line(thresholds.window_s))
            window_end += HOP_LEN
        # keep only samples the next window can still need
        keep_from = max(window_end - WINDOW_LEN, 0)
        if keep_from > buffer_start:
            buffer = buffer[keep_from - buffer_start :]
            buffer_start = keep_from
    log(f"scored {scored} window(s); decisions: {reporter.counts()}")
    return reporter


def mic_blocks(
    seconds: float = DEFAULT_MIC_SECONDS,
    device: Optional[str] = None,
    sample_rate: int = SAMPLE_RATE,
) -> Iterable[np.ndarray]:
    """Yield int16 mono blocks from the host microphone (TD-006: 16 kHz)."""
    try:
        import sounddevice as sd
    except ImportError as exc:  # pragma: no cover - sounddevice is a core dep
        raise InferDepsError(
            f"sounddevice is required for `ultron infer mic` ({exc}) — run `uv sync`"
        ) from exc
    try:
        stream_device = device
        if isinstance(stream_device, str) and stream_device.isdigit():
            stream_device = int(stream_device)
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=HOP_LEN,
            device=stream_device,
        ) as stream:
            deadline = None if seconds <= 0 else time.monotonic() + seconds
            while deadline is None or time.monotonic() < deadline:
                data, _overflow = stream.read(HOP_LEN)
                yield np.asarray(data, dtype=np.int16).reshape(-1)
    except Exception as exc:  # noqa: BLE001 — no mic / bad device is loud
        raise InferError(
            f"cannot capture from the host microphone ({exc}) — check the input "
            "device (`--device`) and the OS microphone permission"
        ) from exc


# ── CLI ──────────────────────────────────────────────────────────────────────

infer_app = typer.Typer(
    name="infer",
    help="Host inference on exported TFLite artifacts (T10): files + live mic.",
)


def _fail(exc: InferError, *, deps: bool = False) -> None:
    typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=3 if deps else 2)


@infer_app.command(name="files")
def infer_files(
    model: Path = typer.Option(
        DEFAULT_MODEL, "--model", "-m", help="Exported artifact (int8 or fp16, T9)."
    ),
    wav: Path = typer.Option(
        ...,
        "--wav",
        "-w",
        help="WAV file or folder (a labeled folder also emits the FR-036 report).",
    ),
    labels: Optional[Path] = typer.Option(
        None,
        "--labels",
        "-l",
        help="Frozen class order (default: labels.json next to the model).",
    ),
    thresholds: Path = typer.Option(
        DEFAULT_THRESHOLDS,
        "--thresholds",
        "-t",
        help="Operating point document (Q-006, from `ultron train sweep`).",
    ),
    checkpoint: Path = typer.Option(
        DEFAULT_CHECKPOINT,
        "--checkpoint",
        "-c",
        help="T8 checkpoint — the PyTorch reference for the FR-036 report.",
    ),
    agreement_report: Path = typer.Option(
        DEFAULT_AGREEMENT_REPORT,
        "--agreement-report",
        help="Where the FR-036 agreement report is written.",
    ),
    no_agreement: bool = typer.Option(
        False,
        "--no-agreement",
        help="Skip the PyTorch agreement report (no checkpoint/torch needed).",
    ),
) -> None:
    """Score WAVs through an exported artifact; print scores + detections (FR-034).

    Features come from the shared C MFCC library (2 s windows, 500 ms hop —
    TD-006), the artifact is executed by tflite_runtime/LiteRT (TD-013), and
    detections are decoded with `model/thresholds.json` (FR-015, Q-006). Over a
    labeled folder a host-TFLite vs PyTorch argmax agreement report is written
    (FR-036).
    """
    try:
        # Cheap, pure checks first (model header → thresholds → class order →
        # inputs) so bad input is reported the same way on every environment;
        # the interpreter is only resolved once the run could otherwise proceed.
        validate_model_file(model)  # FR-034: corrupt artifact fails before anything else
        settings = load_thresholds(thresholds)
        class_labels = resolve_labels(labels, model)
        paths, was_dir = collect_wavs(wav)
        runner = TfliteRunner(model)
        check_class_alignment(class_labels, runner.n_classes, str(model))
        stale = sorted(set(settings.commands) - set(class_labels))
        if stale:
            raise ThresholdsError(
                f"{thresholds}: operating point(s) for untrained class(es) "
                f"{stale} — thresholds.json is out of sync with the artifact's "
                f"class order [{', '.join(class_labels)}]; re-run "
                "`ultron train sweep` (FR-032)"
            )
        mfcc = open_mfcc()
        try:
            results = [
                score_clip(p, runner, mfcc, settings, class_labels, expected_label(p, class_labels))
                for p in paths
            ]
        finally:
            mfcc.close()
    except InferDepsError as exc:
        _fail(exc, deps=True)
        return
    except InferError as exc:
        _fail(exc)
        return

    typer.echo(f"model: {runner.descriptor}")
    typer.echo(f"classes: {' '.join(class_labels)}")
    typer.echo(f"thresholds: {thresholds} (window_s {settings.window_s})")
    for result in results:
        expected = f" — labeled '{result.expected}'" if result.expected else ""
        typer.echo(f"\nclip: {result.path}{expected}")
        for window in result.windows:
            typer.echo(
                f"  [{window.start_s:5.2f}s] "
                f"{format_scores(window.detection, window.scores, class_labels)}"
                f"  →  {window.detection.verdict()} ({window.detection.reason})"
            )
        accepted = len(result.detections)
        summary = ", ".join(f"{k}×{v}" for k, v in result.label_counts().items()) or "none"
        typer.echo(f"  detections: {accepted}/{len(result.windows)} windows — {summary}")

    total_windows = sum(len(r.windows) for r in results)
    total_accepted = sum(len(r.detections) for r in results)
    typer.echo(
        f"\n{len(results)} clip(s), {total_windows} window(s), "
        f"{total_accepted} accepted detection(s)"
    )

    if not was_dir:
        typer.echo("agreement report: skipped (FR-036 applies to a labeled folder)")
        return
    if no_agreement:
        typer.echo("agreement report: skipped (--no-agreement)")
        return
    if not is_labeled(paths, class_labels):
        typer.echo(
            "agreement report: skipped — the folder is not labeled "
            "(expected <label>/*.wav with labels from build/labels.json, FR-036)"
        )
        return
    try:
        markdown, stats = build_agreement(
            results,
            class_labels,
            checkpoint,
            artifact=model,
            descriptor=runner.descriptor,
            threshold_path=thresholds,
            backend=runner.backend,
        )
        report_path = write_report(markdown, agreement_report)
    except InferDepsError as exc:
        _fail(exc, deps=True)
        return
    except InferError as exc:
        _fail(exc)
        return
    typer.echo(
        f"host-TFLite vs PyTorch argmax agreement: {stats['agreement']:.2%} over "
        f"{stats['n_windows']} window(s) ({stats['n_mismatch']} mismatch)"
    )
    if "accuracy" in stats:
        typer.echo(f"host-TFLite accuracy vs folder label: {stats['accuracy']:.2%}")
    typer.secho(f"agreement report: {report_path}", fg=typer.colors.GREEN)


@infer_app.command(name="mic")
def infer_mic(
    model: Path = typer.Option(
        DEFAULT_MODEL, "--model", "-m", help="Exported artifact (int8 or fp16, T9)."
    ),
    labels: Optional[Path] = typer.Option(
        None, "--labels", "-l", help="Frozen class order (default: next to the model)."
    ),
    thresholds: Path = typer.Option(
        DEFAULT_THRESHOLDS, "--thresholds", "-t", help="Operating point document (Q-006)."
    ),
    seconds: float = typer.Option(
        DEFAULT_MIC_SECONDS,
        "--seconds",
        "-s",
        help="How long to listen (0 = until Ctrl-C).",
    ),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    decisions_only: bool = typer.Option(
        False,
        "--decisions-only",
        help="Quiet mode: only accepted decisions, no per-window label stream.",
    ),
) -> None:
    """Score the host mic continuously; stream every window's label (FR-035).

    One line per scored window — timestamp + decoded top label + score — so the
    model's live thinking is visible (``noise noise wake wake la la``); accepted
    detections also print a distinct ``DETECTED …`` line with the wake →
    command association. Reporting only — no actuation and no spec 003 event
    semantics: the device state machine has exactly one owner (spec 003-can-act,
    TD-013). Use ``--decisions-only`` for the quieter decision log.
    """
    try:
        validate_model_file(model)  # FR-034: corrupt artifact fails before anything else
        settings = load_thresholds(thresholds)
        class_labels = resolve_labels(labels, model)
        runner = TfliteRunner(model)
        check_class_alignment(class_labels, runner.n_classes, str(model))
        mfcc = open_mfcc()
    except InferDepsError as exc:
        _fail(exc, deps=True)
        return
    except InferError as exc:
        _fail(exc)
        return

    typer.echo(f"model: {runner.descriptor}")
    typer.echo(f"classes: {' '.join(class_labels)}")
    typer.echo(
        f"listening ({0 if seconds <= 0 else seconds:.0f}s; 2.0s windows / 500ms hop) — "
        f"say 'hey ultron' then a command."
        + (
            " Per-window labels OFF (--decisions-only)."
            if decisions_only
            else " One line per window: [mm:ss.s] <label> <score> ✓ = accepted."
        )
        + " Reporting only: no actuation (TD-013)."
    )
    try:
        run_mic_session(
            mic_blocks(seconds=seconds, device=device),
            runner,
            mfcc,
            settings,
            class_labels,
            log=flush_print,
            stream_windows=not decisions_only,
        )
    except KeyboardInterrupt:
        typer.echo("\nstopped.")
    except InferDepsError as exc:
        _fail(exc, deps=True)
        return
    except InferError as exc:
        _fail(exc)
        return
    finally:
        mfcc.close()
