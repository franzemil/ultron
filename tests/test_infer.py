"""Host inference tests — `ultron infer` (T10, spec 002-can-think).

Order mirrors the task checklist: the interpreter fallback chain (TD-013),
corrupted-artifact detection (FR-034), thresholds decoding (FR-015/Q-006),
windowing through the shared C MFCC (TD-006), the FR-036 agreement report and
the FR-035 mic reporting loop — all of which run with **no microphone and no
TFLite runtime** (a fake ``Interpreter`` class covers the int8 in/out
quantization path using the artifact's own scales).

The end-to-end runs need the heavy export stack and a real interpreter; they
are skipped with an explicit reason when absent ("REQUIRES LOCAL RUN"). The
live-microphone demo is the only genuinely unrunnable-in-CI check and is gated
behind ``ULTRON_MIC_E2E=1``.
"""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

import ultron.infer as infer_mod
from ultron.cli import app
from ultron.infer import (
    NUM_COEFFS,
    TFLITE_MAGIC,
    WINDOW_FRAMES,
    WINDOW_LEN,
    ClipResult,
    CorruptModelError,
    DecisionReporter,
    Detection,
    InferDepsError,
    InferError,
    OperatingPoint,
    Thresholds,
    ThresholdsError,
    TfliteRunner,
    agreement_stats,
    build_agreement,
    check_class_alignment,
    collect_wavs,
    decode_scores,
    decode_window,
    expected_label,
    is_labeled,
    load_thresholds,
    open_mfcc,
    render_agreement_report,
    resolve_interpreter,
    resolve_labels,
    run_mic_session,
    score_clip,
    validate_model_file,
    window_features,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000
#: T8's frozen class order for the initial 4-class set (wake, noise + registry order).
LABELS = ["wake", "noise", "lb", "la"]
runner_cli = CliRunner()

#: Real export stack + a usable interpreter ⇒ the e2e tests can run.
_HAS_TFLITE = True
try:  # a missing runtime must not break unit-test collection
    resolve_interpreter()
except InferDepsError:  # pragma: no cover - environment dependent
    _HAS_TFLITE = False

requires_runtime = pytest.mark.skipif(
    not _HAS_TFLITE,
    reason="no TFLite interpreter (tflite_runtime / ai-edge-litert / tf.lite) — "
    "REQUIRES LOCAL RUN: `uv sync --extra export`",
)


def _conversion_available() -> bool:
    from ultron_model.export import conversion_deps_available

    return conversion_deps_available()


requires_conversion = pytest.mark.skipif(
    not (_conversion_available() and _HAS_TFLITE),
    reason="conversion stack (torch/onnx2tf/tensorflow) or interpreter missing — "
    "REQUIRES LOCAL RUN: `uv sync --extra export`",
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def write_wav(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.astype("<i2").tobytes())
    return path


def tone(freq: float, seconds: float = 2.5) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.6 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


def dummy_model(path: Path, payload: bytes = b"") -> Path:
    """A file with a valid TFLite header (the fake interpreter ignores the body)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x00" + TFLITE_MAGIC + payload + b"\x00" * 32)
    return path


def thresholds_doc(**overrides) -> dict:
    doc = {
        "wake": 0.80,
        "margin_wake": 0.10,
        "commands": {"la": {"threshold": 0.80, "margin": 0.10}},
        "window_s": 5.0,
    }
    doc.update(overrides)
    return doc


def make_thresholds() -> Thresholds:
    return Thresholds(
        wake=0.80,
        margin_wake=0.10,
        commands={"lb": OperatingPoint(0.80, 0.10), "la": OperatingPoint(0.85, 0.15)},
        window_s=5.0,
        path=Path("model/thresholds.json"),
    )


def fake_interpreter_cls(
    record: dict,
    *,
    in_shape: tuple[int, ...] = (1, NUM_COEFFS, WINDOW_FRAMES),
    in_dtype=np.int8,
    in_quant: tuple[float, int] = (0.5, 4),
    n_classes: int = len(LABELS),
    out_dtype=np.int8,
    out_quant: tuple[float, int] = (1 / 255, -128),
):
    """A minimal ``tf.lite.Interpreter`` stand-in (records tensors it receives).

    The output is deterministic given the quantized input (|sum| mod C), so a
    test can verify both the input quantization and the output dequantization
    against the artifact's own scales.
    """

    class FakeInterpreter:
        def __init__(self, model_content=None, **_kwargs):
            record["model_content"] = model_content
            record.setdefault("inputs", [])

        def allocate_tensors(self) -> None:
            record["allocated"] = True

        def get_input_details(self):
            return [
                {
                    "index": 0,
                    "shape": np.array(in_shape),
                    "dtype": np.dtype(in_dtype),
                    "quantization": in_quant,
                }
            ]

        def get_output_details(self):
            return [
                {
                    "index": 1,
                    "shape": np.array((1, n_classes)),
                    "dtype": np.dtype(out_dtype),
                    "quantization": out_quant,
                }
            ]

        def set_tensor(self, index, data):
            record["inputs"].append(np.array(data))
            record["last_index"] = index

        def invoke(self) -> None:
            record["invokes"] = record.get("invokes", 0) + 1

        def get_tensor(self, index):
            x = record["inputs"][-1]
            winner = int(np.abs(x.astype(np.int32)).sum()) % n_classes
            raw = np.full(n_classes, int(out_quant[1]), dtype=np.dtype(out_dtype))
            raw[winner] = 127 if np.issubdtype(np.dtype(out_dtype), np.signedinteger) else 1
            return raw.reshape(1, n_classes)

    return FakeInterpreter


class StubRunner:
    """A runner whose scores are scripted (no artifact, no runtime)."""

    backend = "stub"

    def __init__(self, script: list[list[float]], labels=LABELS):
        self.descriptor = "stub.tflite · scripted"
        self.n_classes = len(labels)
        self._script = script
        self.calls = 0

    def scores(self, features_nwc):
        rows = []
        for _ in features_nwc:
            row = self._script[min(self.calls, len(self._script) - 1)]
            self.calls += 1
            rows.append(np.asarray(row, dtype=np.float32))
        return np.stack(rows)


def probs(**scores: float) -> list[float]:
    """Build a score row in LABELS order from keyword probabilities."""
    row = np.zeros(len(LABELS), dtype=np.float32)
    for label, value in scores.items():
        row[LABELS.index(label)] = value
    return row.tolist()


# ── TD-013: interpreter fallback chain ───────────────────────────────────────


def test_resolve_interpreter_uses_first_available_link():
    class Interp:
        pass

    def loader(name):
        return {"tflite_runtime.interpreter": type("m", (), {"Interpreter": Interp})}.get(name)

    assert resolve_interpreter(loader) is Interp


def test_resolve_interpreter_falls_back_to_litert():
    class LiteRTInterp:
        pass

    module = type("m", (), {"Interpreter": LiteRTInterp})

    def loader(name):
        return {  # tflite_runtime has no macOS arm64 wheels → next link wins
            "ai_edge_litert.interpreter": module,
        }.get(name)

    assert resolve_interpreter(loader) is LiteRTInterp


def test_resolve_interpreter_falls_back_to_tf_lite():
    class TfInterp:
        pass

    def loader(name):
        return {"tensorflow.lite": type("m", (), {"Interpreter": TfInterp})}.get(name)

    assert resolve_interpreter(loader) is TfInterp


def test_resolve_interpreter_reports_every_link_and_the_fix():
    with pytest.raises(InferDepsError) as exc:
        resolve_interpreter(lambda name: None)
    message = str(exc.value)
    assert "tflite_runtime.interpreter" in message
    assert "ai_edge_litert.interpreter" in message
    assert "tensorflow.lite" in message
    assert "uv sync --extra export" in message
    assert "REQUIRES A LOCAL RUN" in message


def test_resolve_interpreter_skips_module_without_interpreter():
    class Interp:
        pass

    def loader(name):
        if name == "tflite_runtime.interpreter":
            return type("m", (), {})  # importable but no Interpreter
        return {"tensorflow.lite": type("m", (), {"Interpreter": Interp})}.get(name)

    assert resolve_interpreter(loader) is Interp


# ── FR-034: corrupted artifact fails loudly ─────────────────────────────────


def test_validate_model_file_rejects_missing(tmp_path):
    with pytest.raises(InferError, match="model not found"):
        validate_model_file(tmp_path / "nope.tflite")


def test_validate_model_file_rejects_empty(tmp_path):
    path = tmp_path / "empty.tflite"
    path.write_bytes(b"")
    with pytest.raises(CorruptModelError, match="not a TFLite flatbuffer"):
        validate_model_file(path)


def test_validate_model_file_rejects_wrong_magic(tmp_path):
    path = tmp_path / "fake.onnx"
    path.write_bytes(b"\x00\x00\x00\x00ONNX" + b"\x00" * 16)
    with pytest.raises(CorruptModelError, match="b'ONNX'"):
        validate_model_file(path)


def test_validate_model_file_accepts_valid_header(tmp_path):
    path = dummy_model(tmp_path / "ok.tflite")
    assert validate_model_file(path)[4:8] == TFLITE_MAGIC


def test_runner_rejects_corrupt_artifact_before_touching_a_runtime(tmp_path, monkeypatch):
    """A corrupt artifact fails on its own — independently of the runtime (FR-034)."""
    path = tmp_path / "corrupt.tflite"
    path.write_bytes(b"\x00\x00\x00\x00XXXX" + b"\x00" * 32)

    def boom(_loader=None):  # pragma: no cover - must never be called
        raise AssertionError("interpreter must not be resolved for a corrupt artifact")

    monkeypatch.setattr(infer_mod, "resolve_interpreter", boom)
    with pytest.raises(CorruptModelError, match="re-export"):
        TfliteRunner(path)


def test_runner_wraps_interpreter_load_failure(tmp_path):
    """Valid header, unparseable body ⇒ loud corruption error, not a traceback."""

    class Exploding:
        def __init__(self, **_kwargs):
            raise ValueError("The model is not a valid Flatbuffer buffer")

    with pytest.raises(CorruptModelError, match="valid Flatbuffer"):
        TfliteRunner(dummy_model(tmp_path / "trunc.tflite"), interpreter_cls=Exploding)


def test_runner_wraps_invoke_failure(tmp_path):
    record: dict = {}
    cls = fake_interpreter_cls(record)
    original = cls.invoke

    def invoke(self):
        raise RuntimeError("OP resolver failed")

    cls.invoke = invoke
    runner = TfliteRunner(dummy_model(tmp_path / "m.tflite"), interpreter_cls=cls)
    with pytest.raises(CorruptModelError, match="invoke"):
        runner.scores(np.zeros((1, WINDOW_FRAMES, NUM_COEFFS), np.float32))


# ── int8 in/out artifacts use their own scales (US-009 AC1) ─────────────────


def test_int8_runner_quantizes_with_the_artifacts_own_scales(tmp_path):
    """int8 in/out: round(x/scale)+zp in, (y-zp)*scale out — the T9 rule."""
    record: dict = {}
    cls = fake_interpreter_cls(record, in_quant=(0.5, 4), out_quant=(1 / 255, -128))
    runner = TfliteRunner(dummy_model(tmp_path / "int8.tflite"), interpreter_cls=cls)
    assert runner.quantized is True
    assert runner.layout == "NCW"
    features = (np.arange(2 * WINDOW_FRAMES * NUM_COEFFS, dtype=np.float32).reshape(
        2, WINDOW_FRAMES, NUM_COEFFS
    ) % 60) - 30

    scores = runner.scores(features)

    # NCW artifact ⇒ the runner transposes NWC features before feeding them
    fed = record["inputs"][0]
    assert fed.shape == (1, NUM_COEFFS, WINDOW_FRAMES)
    expected = np.clip(np.round(features[0].T / 0.5) + 4, -128, 127).astype(np.int8)
    np.testing.assert_array_equal(fed[0], expected)
    # deterministic fake output: winner = |sum| % C, dequantized with its scale
    winner = int(np.abs(expected.astype(np.int32)).sum()) % len(LABELS)
    assert scores.shape == (2, len(LABELS))
    assert scores[0].argmax() == winner
    assert scores[0][winner] == pytest.approx((127 + 128) / 255, abs=1e-6)
    assert scores[0][(winner + 1) % len(LABELS)] == pytest.approx(0.0, abs=1e-6)


def test_nwc_input_layout_is_fed_unchanged(tmp_path):
    record: dict = {}
    cls = fake_interpreter_cls(record, in_shape=(1, WINDOW_FRAMES, NUM_COEFFS))
    runner = TfliteRunner(dummy_model(tmp_path / "nwc.tflite"), interpreter_cls=cls)
    assert runner.layout == "NWC"
    features = np.ones((1, WINDOW_FRAMES, NUM_COEFFS), dtype=np.float32)
    runner.scores(features)
    assert record["inputs"][0].shape == (1, WINDOW_FRAMES, NUM_COEFFS)


def test_float16_runner_passes_float32_through(tmp_path):
    record: dict = {}
    cls = fake_interpreter_cls(
        record,
        in_shape=(1, WINDOW_FRAMES, NUM_COEFFS),
        in_dtype=np.float16,
        in_quant=(0.0, 0),
        out_dtype=np.float16,
        out_quant=(0.0, 0),
    )
    runner = TfliteRunner(dummy_model(tmp_path / "fp16.tflite"), interpreter_cls=cls)
    assert runner.quantized is False
    features = np.full((1, WINDOW_FRAMES, NUM_COEFFS), 0.25, dtype=np.float32)
    runner.scores(features)
    fed = record["inputs"][0]
    assert fed.dtype == np.float16
    np.testing.assert_allclose(fed.astype(np.float32), features, atol=1e-3)


def test_runner_rejects_unexpected_input_signature(tmp_path):
    record: dict = {}
    cls = fake_interpreter_cls(record, in_shape=(1, 40, 40))
    with pytest.raises(InferError, match="unexpected input signature"):
        TfliteRunner(dummy_model(tmp_path / "bad.tflite"), interpreter_cls=cls)


def test_runner_descriptor_reports_quantization_and_backend(tmp_path):
    record: dict = {}
    cls = fake_interpreter_cls(record)
    runner = TfliteRunner(dummy_model(tmp_path / "model_int8.tflite"), interpreter_cls=cls)
    text = runner.descriptor
    assert "int8 in/out" in text and "input scale 0.50000" in text
    assert "NCW" in text and f"{len(LABELS)} classes" in text


def test_runner_rejects_wrong_feature_shape(tmp_path):
    record: dict = {}
    runner = TfliteRunner(dummy_model(tmp_path / "m.tflite"), interpreter_cls=fake_interpreter_cls(record))
    with pytest.raises(InferError, match="expected MFCC windows"):
        runner.scores(np.zeros((2, 10, 10), np.float32))


# ── Thresholds (Q-006) + decoding (FR-015) ──────────────────────────────────


def test_load_thresholds_reads_the_q006_schema(tmp_path):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(thresholds_doc()), encoding="utf-8")
    settings = load_thresholds(path)
    assert settings.wake == pytest.approx(0.80)
    assert settings.margin_wake == pytest.approx(0.10)
    assert settings.window_s == pytest.approx(5.0)  # seconds, not a probability
    assert settings.commands["la"] == OperatingPoint(0.80, 0.10)


def test_verisoned_thresholds_document_loads():
    """The repo's versioned operating point (model/thresholds.json) is valid Q-006."""
    settings = load_thresholds(REPO_ROOT / "model" / "thresholds.json")
    assert settings.window_s > 1  # 5.0 s listening window
    assert settings.commands  # at least one trained command


def test_load_thresholds_missing_file_names_the_fix(tmp_path):
    with pytest.raises(ThresholdsError, match="ultron train sweep"):
        load_thresholds(tmp_path / "nope.json")


def test_load_thresholds_missing_key(tmp_path):
    doc = thresholds_doc()
    del doc["window_s"]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ThresholdsError, match="window_s"):
        load_thresholds(path)


def test_load_thresholds_rejects_out_of_range_probability(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(thresholds_doc(wake=1.4)), encoding="utf-8")
    with pytest.raises(ThresholdsError, match="between 0 and 1|probability"):
        load_thresholds(path)


def test_load_thresholds_rejects_bad_window_s(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(thresholds_doc(window_s=0)), encoding="utf-8")
    with pytest.raises(ThresholdsError, match="seconds > 0"):
        load_thresholds(path)


def test_load_thresholds_rejects_non_object_commands(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(thresholds_doc(commands=[1, 2])), encoding="utf-8")
    with pytest.raises(ThresholdsError, match="commands"):
        load_thresholds(path)


def test_load_thresholds_rejects_incomplete_command_entry(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(thresholds_doc(commands={"la": {"threshold": 0.8}})), encoding="utf-8")
    with pytest.raises(ThresholdsError, match="margin"):
        load_thresholds(path)


def test_load_thresholds_rejects_unparseable_json(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(ThresholdsError, match="cannot parse"):
        load_thresholds(path)


def test_decode_accepts_confident_winner():
    settings = make_thresholds()
    detection = decode_window(np.array(probs(wake=0.0, lb=0.92, la=0.03, noise=0.05)), LABELS, settings)
    assert detection.accepted and detection.label == "lb"
    assert "accepted" in detection.reason and "0.920" in detection.reason


def test_decode_rejects_below_threshold():
    settings = make_thresholds()
    detection = decode_window(np.array(probs(lb=0.70, la=0.05, noise=0.25)), LABELS, settings)
    assert not detection.accepted and detection.label is None
    assert "score 0.700 < threshold 0.80" in detection.reason


def test_decode_rejects_thin_margin():
    settings = make_thresholds()
    detection = decode_window(np.array(probs(lb=0.90, la=0.85, noise=0.04)), LABELS, settings)
    assert not detection.accepted
    assert "lead 0.050 < margin 0.10" in detection.reason


def test_decode_uses_per_class_thresholds():
    """FR-015: each class carries its own threshold/margin (la is stricter)."""
    settings = make_thresholds()
    accepted_lb = decode_window(np.array(probs(lb=0.82, la=0.02)), LABELS, settings)
    rejected_la = decode_window(np.array(probs(la=0.82, lb=0.02)), LABELS, settings)
    assert accepted_lb.accepted  # 0.82 ≥ lb's 0.80 / lead 0.80 ≥ 0.10
    assert not rejected_la.accepted  # 0.82 < la's 0.85
    assert rejected_la.operating_point == OperatingPoint(0.85, 0.15)


def test_decode_wake_uses_the_wake_operating_point():
    settings = make_thresholds()
    accepted = decode_window(np.array(probs(wake=0.85, lb=0.02)), LABELS, settings)
    rejected = decode_window(np.array(probs(wake=0.70, lb=0.02)), LABELS, settings)
    assert accepted.accepted and accepted.label == "wake"
    assert not rejected.accepted


def test_decode_never_accepts_the_null_class():
    """`noise` is the null class — no operating point, never a detection."""
    settings = make_thresholds()
    detection = decode_window(np.array(probs(noise=0.99, lb=0.005)), LABELS, settings)
    assert not detection.accepted
    assert "null class" in detection.reason


def test_decode_rejects_untrained_class_without_operating_point():
    """A class without an operating point cannot fire (FR-032 trained subset)."""
    settings = make_thresholds()
    detection = decode_window(
        np.array(probs(lb=0.99, la=0.001, noise=0.001)),
        ["wake", "noise", "gd", "la"],
        settings,
    )
    assert not detection.accepted
    assert detection.argmax_label == "gd"  # predicted class has no operating point
    assert "no operating point for 'gd'" in detection.reason
    assert "FR-032" in detection.reason


def test_decode_rejects_null_class_before_any_threshold_check():
    settings = make_thresholds()
    detection = decode_window(np.array(probs(noise=1.0, lb=0.0, la=0.0)), LABELS, settings)
    assert not detection.accepted
    assert "null class" in detection.reason


def test_decode_rejects_label_count_mismatch():
    with pytest.raises(InferError, match="class order"):
        decode_window(np.array([0.2, 0.8]), ["wake"], make_thresholds())


def test_decode_scores_decodes_every_row():
    settings = make_thresholds()
    matrix = np.array([probs(lb=0.95), probs(noise=0.95), probs(la=0.99)])
    detections = decode_scores(matrix, LABELS, settings)
    assert [d.accepted for d in detections] == [True, False, True]
    assert [d.label for d in detections] == ["lb", None, "la"]


# ── Windowing through the shared C MFCC (TD-006) ────────────────────────────


@pytest.fixture(scope="module")
def mfcc():
    extractor = open_mfcc()
    yield extractor
    extractor.close()


def test_window_features_two_second_windows_with_500ms_hop(mfcc):
    features, starts = window_features(tone(400.0, 2.5), mfcc)
    assert features.shape == (2, WINDOW_FRAMES, NUM_COEFFS)
    assert features.dtype == np.float32
    assert starts == [0, 8000]  # 500 ms hop
    assert not np.isnan(features).any()


def test_window_features_exact_fit_single_window(mfcc):
    features, starts = window_features(tone(400.0, 2.0), mfcc)
    assert features.shape[0] == 1 and starts == [0]


def test_window_features_short_clip_is_zero_padded(mfcc):
    features, starts = window_features(tone(400.0, 0.5), mfcc)
    assert features.shape == (1, WINDOW_FRAMES, NUM_COEFFS)
    assert starts == [0]


def test_window_features_distinguishes_tones(mfcc):
    """Features come from the real C MFCC: different tones ⇒ different rows."""
    low, _ = window_features(tone(400.0, 2.0), mfcc)
    high, _ = window_features(tone(1600.0, 2.0), mfcc)
    assert not np.allclose(low, high)


# ── Clip collection / labels ────────────────────────────────────────────────


def test_collect_wavs_single_file(tmp_path):
    wav = write_wav(tmp_path / "clip.wav", tone(400.0, 0.2))
    paths, was_dir = collect_wavs(wav)
    assert paths == [wav] and was_dir is False


def test_collect_wavs_folder_is_recursive_and_sorted(tmp_path):
    for label in ("lb", "la"):
        write_wav(tmp_path / "f" / label / f"{label}_b.wav", tone(400.0, 0.2))
        write_wav(tmp_path / "f" / label / f"{label}_a.wav", tone(400.0, 0.2))
    paths, was_dir = collect_wavs(tmp_path / "f")
    assert was_dir is True
    assert [p.name for p in paths] == ["la_a.wav", "la_b.wav", "lb_a.wav", "lb_b.wav"]


def test_collect_wavs_empty_folder_fails_loudly(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(InferError, match="no .wav files"):
        collect_wavs(tmp_path / "empty")


def test_collect_wavs_missing_path_fails_loudly(tmp_path):
    with pytest.raises(InferError, match="not found"):
        collect_wavs(tmp_path / "nope")


def test_collect_wavs_rejects_non_wav_file(tmp_path):
    other = tmp_path / "clip.mp3"
    other.write_bytes(b"x")
    with pytest.raises(InferError, match="not a .wav"):
        collect_wavs(other)


def test_expected_label_and_is_labeled(tmp_path):
    labeled = [write_wav(tmp_path / "lb" / "a.wav", tone(400.0, 0.2))]
    assert expected_label(labeled[0], LABELS) == "lb"
    assert is_labeled(labeled, LABELS) is True
    other = write_wav(tmp_path / "misc" / "b.wav", tone(400.0, 0.2))
    assert expected_label(other, LABELS) is None
    assert is_labeled([other], LABELS) is False


def test_resolve_labels_prefers_explicit_path(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(LABELS), encoding="utf-8")
    assert resolve_labels(path, tmp_path / "model.tflite") == LABELS


def test_resolve_labels_defaults_next_to_the_model(tmp_path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "labels.json").write_text(json.dumps(LABELS), encoding="utf-8")
    assert resolve_labels(None, tmp_path / "build" / "model_int8.tflite") == LABELS


def test_resolve_labels_missing_names_the_export(tmp_path):
    with pytest.raises(InferError, match="ultron export tflite"):
        resolve_labels(None, tmp_path / "build" / "model.tflite")


def test_resolve_labels_rejects_garbage(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps({"wake": 1}), encoding="utf-8")
    with pytest.raises(InferError, match="array of label strings"):
        resolve_labels(path, tmp_path / "m.tflite")


def test_check_class_alignment_names_both_sides():
    with pytest.raises(InferError, match="3 label\\(s\\).*emits 4 classes"):
        check_class_alignment(["wake", "noise", "la"], 4, "artifact")
    check_class_alignment(LABELS, 4, "artifact")  # no raise


# ── FR-036: agreement computation + report ─────────────────────────────────


def test_agreement_stats_all_match():
    tflite = np.array([0, 1, 2, 3])
    stats = agreement_stats(tflite, tflite.copy(), LABELS, expected_idx=tflite)
    assert stats["agreement"] == 1.0
    assert stats["n_windows"] == 4 and stats["n_mismatch"] == 0
    assert stats["accuracy"] == 1.0
    assert set(stats["per_label"]) == set(LABELS)
    assert all(entry["windows"] == 1 for entry in stats["per_label"].values())


def test_agreement_stats_reports_mismatches_and_per_class():
    tflite = np.array([0, 3, 3, 3])
    torch_ = np.array([0, 3, 3, 2])
    stats = agreement_stats(tflite, torch_, LABELS, expected_idx=np.array([0, 3, 3, 3]))
    assert stats["agreement"] == pytest.approx(0.75)
    assert stats["n_mismatch"] == 1
    assert stats["mismatch_rows"] == [3]
    assert stats["per_label"]["la"] == {"windows": 3, "agreement": pytest.approx(2 / 3), "expected": 3}


def test_agreement_stats_rejects_shape_and_empty():
    with pytest.raises(InferError, match="equal-length"):
        agreement_stats(np.array([0, 1]), np.array([0]), LABELS)
    with pytest.raises(InferError, match="zero windows"):
        agreement_stats(np.array([], dtype=np.int64), np.array([], dtype=np.int64), LABELS)


def test_render_agreement_report_contains_every_claim():
    stats = {
        "n_windows": 4,
        "n_mismatch": 1,
        "agreement": 0.75,
        "per_label": {"lb": {"windows": 4, "agreement": 0.75}},
        "accuracy": 0.75,
        "n_labeled": 4,
        "mismatch_rows": [2],
        "versions": {"python": "3.12.9", "interpreter": "ai_edge_litert"},
    }
    rows = [
        {
            "clip": "dataset/lb/lb_xiao_01.wav",
            "start_s": 0.5,
            "tflite": "lb",
            "tflite_score": 0.9,
            "torch": "la",
            "torch_score": 0.7,
            "expected": "lb",
        }
    ]
    report = render_agreement_report(
        artifact=Path("build/model_int8.tflite"),
        descriptor="model_int8.tflite · int8 in/out",
        checkpoint=Path("build/checkpoint.pt"),
        labels=LABELS,
        threshold_path=Path("model/thresholds.json"),
        stats=stats,
        rows=rows,
    )
    assert "host-TFLite vs PyTorch argmax agreement: 75.00%" in report
    assert "BELOW NFR-007 GATE" in report
    assert "wake, noise, lb, la" in report
    assert "| lb | 4 | 75.00% |" in report
    assert "dataset/lb/lb_xiao_01.wav" in report and "0.50s" in report
    assert "ai_edge_litert" in report
    assert "no actuation" not in report.lower()


def test_build_agreement_end_to_end_with_a_stub_reference(tmp_path, monkeypatch):
    """FR-036 wiring: scored clips + (stubbed) PyTorch reference → markdown."""
    clips = [
        ClipResult(
            path=Path("dataset/lb/a.wav"),
            expected="lb",
            windows=[],
            feature_rows=np.zeros((1, WINDOW_FRAMES, NUM_COEFFS), np.float32),
        )
    ]
    detection = Detection("lb", 0.9, 0.05, OperatingPoint(0.8, 0.1), True, "accepted")
    clips[0].windows.append(
        infer_mod.WindowResult(start_s=0.0, scores=np.array(probs(lb=0.9, la=0.05)), detection=detection)
    )
    # PyTorch argues for `la` → the report must flag the mismatch
    monkeypatch.setattr(
        infer_mod,
        "torch_reference_scores",
        lambda features, checkpoint=None: (np.array([probs(la=0.8, lb=0.1)]), LABELS),
    )
    markdown, stats = build_agreement(
        clips,
        LABELS,
        tmp_path / "checkpoint.pt",
        artifact=tmp_path / "model_int8.tflite",
        descriptor="stub",
        threshold_path=tmp_path / "thresholds.json",
        backend="stub",
    )
    assert stats["agreement"] == 0.0 and stats["n_mismatch"] == 1
    assert "0.00%" in markdown
    assert "| `dataset/lb/a.wav` | 0.00s | lb (0.900) | la (0.800) | lb |" in markdown


def test_build_agreement_rejects_a_reordered_checkpoint(tmp_path, monkeypatch):
    clips = [
        ClipResult(
            path=Path("dataset/lb/a.wav"),
            expected="lb",
            windows=[infer_mod.WindowResult(0.0, np.array(probs(lb=0.9)), Detection("lb", 0.9, 0.0, None, True, ""))],
            feature_rows=np.zeros((1, WINDOW_FRAMES, NUM_COEFFS), np.float32),
        )
    ]
    monkeypatch.setattr(
        infer_mod,
        "torch_reference_scores",
        lambda features, checkpoint=None: (np.array([probs(lb=0.9)]), ["noise", "wake", "lb", "la"]),
    )
    with pytest.raises(InferError, match="class order mismatch"):
        build_agreement(
            clips,
            LABELS,
            tmp_path / "checkpoint.pt",
            artifact=tmp_path / "m.tflite",
            descriptor="stub",
        )


def test_torch_reference_without_a_checkpoint_names_the_fix(tmp_path):
    with pytest.raises(InferError, match="ultron train run"):
        infer_mod.torch_reference_scores(np.zeros((1, WINDOW_FRAMES, NUM_COEFFS), np.float32), tmp_path / "nope.pt")


# ── FR-035: mic decision reporting (no mic needed) ──────────────────────────


def _detection(label: str, score: float = 0.95) -> Detection:
    return Detection(label, score, 0.01, OperatingPoint(0.8, 0.1), True, "accepted")


def test_reporter_pairs_a_command_with_a_wake_inside_the_window():
    reporter = DecisionReporter(window_s=5.0)
    assert reporter.add(1.0, _detection("wake")).within_window is False
    decision = reporter.add(2.5, _detection("la", 0.97))
    assert decision.label == "la"
    assert decision.wake_t_s == pytest.approx(1.0)
    assert decision.after_wake_s == pytest.approx(1.5)
    assert decision.within_window is True
    assert "wake → la" in decision.line(5.0)
    assert "1.50s after wake" in decision.line(5.0)


def test_reporter_marks_a_command_outside_the_window_as_unpaired():
    reporter = DecisionReporter(window_s=5.0)
    reporter.add(0.0, _detection("wake"))
    decision = reporter.add(6.0, _detection("lb"))
    assert decision.wake_t_s is None
    assert decision.within_window is False
    assert "no wake within the last 5.0s" in decision.line(5.0)


def test_reporter_marks_a_command_before_any_wake_as_unpaired():
    reporter = DecisionReporter(window_s=5.0)
    decision = reporter.add(2.0, _detection("la"))
    assert decision.wake_t_s is None
    assert "no wake within the last" in decision.line(5.0)


def test_reporter_forgets_a_stale_wake():
    reporter = DecisionReporter(window_s=2.0)
    reporter.add(0.0, _detection("wake"))
    assert reporter.add(3.0, _detection("la")).wake_t_s is None
    # a fresh wake re-arms the association
    reporter.add(4.0, _detection("wake"))
    assert reporter.add(4.5, _detection("lb")).within_window is True


def test_reporter_ignores_rejected_windows_and_counts_decisions():
    reporter = DecisionReporter(window_s=5.0)
    rejected = Detection("la", 0.4, 0.3, OperatingPoint(0.8, 0.1), False, "rejected")
    assert reporter.add(0.0, rejected) is None
    reporter.add(1.0, _detection("wake"))
    reporter.add(2.0, _detection("la"))
    reporter.add(9.0, _detection("lb"))
    assert reporter.counts() == {"wake": 1, "commands_after_wake": 1, "commands_alone": 1}


def test_reporter_rejects_bad_window_s():
    with pytest.raises(InferError, match="window_s"):
        DecisionReporter(window_s=0)


def test_run_mic_session_scores_a_fixed_stream(mfcc):
    """6 s of audio → 9 windows (2 s window, 500 ms hop) — TD-006 timing."""
    script = [probs(wake=0.97), probs(la=0.96)] + [probs(noise=0.98)] * 7
    stub = StubRunner(script)
    lines: list[str] = []
    audio = np.zeros(6 * SAMPLE_RATE, dtype=np.int16)
    reporter = run_mic_session(
        [audio[i : i + 8000] for i in range(0, len(audio), 8000)],
        stub,
        mfcc,
        make_thresholds(),
        LABELS,
        log=lines.append,
    )
    assert stub.calls == 9  # windows ending at 2.0 s … 6.0 s
    assert len(reporter.events) == 2
    assert reporter.events[0].label == "wake"
    assert reporter.events[1].within_window is True
    assert "wake → la" in lines[-2]
    assert "scored 9 window(s)" in lines[-1]


def test_run_mic_session_is_block_size_independent(mfcc):
    script = [probs(wake=0.97), probs(la=0.96)] + [probs(noise=0.98)] * 7
    audio = np.zeros(6 * SAMPLE_RATE, dtype=np.int16)

    def run(block: int):
        stub = StubRunner(script)
        lines: list[str] = []
        reporter = run_mic_session(
            [audio[i : i + block] for i in range(0, len(audio), block)],
            stub,
            mfcc,
            make_thresholds(),
            LABELS,
            log=lines.append,
        )
        return stub.calls, [(e.t_s, e.label) for e in reporter.events]

    assert run(8000) == run(3200) == run(16000)


# ── CLI wiring ──────────────────────────────────────────────────────────────


def test_cli_infer_help():
    result = runner_cli.invoke(app, ["infer", "--help"])
    assert result.exit_code == 0
    assert "files" in result.output and "mic" in result.output


def test_cli_infer_files_help_documents_the_contract():
    result = runner_cli.invoke(app, ["infer", "files", "--help"])
    assert result.exit_code == 0
    for option in ("--model", "--wav", "--labels", "--thresholds", "--checkpoint"):
        assert option in result.output


def test_cli_infer_mic_help():
    result = runner_cli.invoke(app, ["infer", "mic", "--help"])
    assert result.exit_code == 0
    assert "--seconds" in result.output and "--device" in result.output


def test_cli_corrupt_artifact_exits_two_without_a_traceback(tmp_path):
    """FR-034: a deliberately corrupted artifact fails LOUDLY (exit 2)."""
    bad = tmp_path / "model_int8.tflite"
    bad.write_bytes(b"\x00\x00\x00\x00XXXX" + b"\x00" * 64)
    wav = write_wav(tmp_path / "lb" / "a.wav", tone(400.0, 0.2))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(LABELS), encoding="utf-8")
    result = runner_cli.invoke(
        app,
        ["infer", "files", "--model", str(bad), "--wav", str(wav), "--labels", str(labels)],
    )
    assert result.exit_code == 2
    assert "not a TFLite flatbuffer" in result.output
    assert "Traceback" not in result.output


def test_cli_corrupt_artifact_fails_before_the_runtime_is_needed(tmp_path, monkeypatch):
    """The header check precedes interpreter resolution (works on a light env)."""

    def boom(_loader=None):  # pragma: no cover - must not run
        raise AssertionError("runtime must not be resolved")

    monkeypatch.setattr(infer_mod, "resolve_interpreter", boom)
    bad = tmp_path / "model.tflite"
    bad.write_bytes(b"")
    result = runner_cli.invoke(app, ["infer", "files", "--model", str(bad), "--wav", str(tmp_path)])
    assert result.exit_code == 2
    assert "not a TFLite flatbuffer" in result.output


def test_cli_missing_thresholds_exits_two_naming_the_sweep(tmp_path):
    wav = write_wav(tmp_path / "lb" / "a.wav", tone(400.0, 0.2))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(LABELS), encoding="utf-8")
    model = dummy_model(tmp_path / "model.tflite")
    result = runner_cli.invoke(
        app,
        [
            "infer", "files",
            "--model", str(model),
            "--wav", str(wav),
            "--labels", str(labels),
            "--thresholds", str(tmp_path / "missing.json"),
        ],
    )
    assert result.exit_code == 2
    assert "ultron train sweep" in result.output


def test_cli_missing_labels_exits_two_naming_the_export(tmp_path):
    model = dummy_model(tmp_path / "build" / "model.tflite")
    wav = write_wav(tmp_path / "lb" / "a.wav", tone(400.0, 0.2))
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(thresholds_doc()), encoding="utf-8")
    result = runner_cli.invoke(
        app,
        ["infer", "files", "--model", str(model), "--wav", str(wav), "--thresholds", str(path)],
    )
    assert result.exit_code == 2
    assert "ultron export tflite" in result.output


def test_cli_dependency_failure_exits_three(tmp_path, monkeypatch):
    def no_runtime(_loader=None):
        raise InferDepsError("no TFLite interpreter available — `uv sync --extra export`")

    monkeypatch.setattr(infer_mod, "resolve_interpreter", no_runtime)
    model = dummy_model(tmp_path / "build" / "model.tflite")
    (tmp_path / "build" / "labels.json").write_text(json.dumps(LABELS), encoding="utf-8")
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(thresholds_doc()), encoding="utf-8")
    wav = write_wav(tmp_path / "lb" / "a.wav", tone(400.0, 0.2))
    result = runner_cli.invoke(
        app,
        ["infer", "files", "--model", str(model), "--wav", str(wav), "--thresholds", str(path)],
    )
    assert result.exit_code == 3
    assert "uv sync --extra export" in result.output


def test_cli_prints_scores_detections_and_the_agreement_report(tmp_path, monkeypatch):
    """FR-034 + FR-036 CLI wiring, with a stub runner and stub reference."""
    dataset = tmp_path / "dataset"
    write_wav(dataset / "lb" / "lb_1.wav", tone(800.0, 2.5))
    write_wav(dataset / "lb" / "lb_2.wav", tone(800.0, 2.5))
    labels_path = tmp_path / "build" / "labels.json"
    labels_path.parent.mkdir()
    labels_path.write_text(json.dumps(LABELS), encoding="utf-8")
    model = dummy_model(tmp_path / "build" / "model_int8.tflite")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(
        json.dumps(thresholds_doc(commands={"lb": {"threshold": 0.80, "margin": 0.10}})),
        encoding="utf-8",
    )
    report_path = tmp_path / "build" / "infer_agreement.md"

    def stub_runner(_path):
        return StubRunner([probs(lb=0.93, la=0.04, wake=0.02, noise=0.01)])

    monkeypatch.setattr(infer_mod, "TfliteRunner", stub_runner)
    monkeypatch.setattr(
        infer_mod,
        "torch_reference_scores",
        lambda features, checkpoint=None: (
            np.tile(np.array(probs(lb=0.9, la=0.05)), (len(features), 1)),
            LABELS,
        ),
    )
    result = runner_cli.invoke(
        app,
        [
            "infer", "files",
            "--model", str(model),
            "--wav", str(dataset),
            "--thresholds", str(thresholds_path),
            "--checkpoint", str(tmp_path / "checkpoint.pt"),
            "--agreement-report", str(report_path),
        ],
    )
    assert result.exit_code == 0, result.output
    # FR-034: per-class scores + the decoded detection
    assert "lb 0.9300" in result.output
    assert "→  lb (accepted: score 0.930 ≥ 0.80" in result.output
    assert "detections: 2/2 windows — lb×2" in result.output
    # FR-036: the agreement report is written and summarised
    assert "host-TFLite vs PyTorch argmax agreement: 100.00%" in result.output
    assert report_path.is_file()
    assert "host-TFLite vs PyTorch argmax agreement: 100.00%" in report_path.read_text()


def test_cli_skips_the_agreement_report_for_an_unlabeled_folder(tmp_path, monkeypatch):
    write_wav(tmp_path / "misc" / "a.wav", tone(800.0, 0.3))
    labels_path = tmp_path / "build" / "labels.json"
    labels_path.parent.mkdir()
    labels_path.write_text(json.dumps(LABELS), encoding="utf-8")
    model = dummy_model(tmp_path / "build" / "model_int8.tflite")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds_doc()), encoding="utf-8")
    monkeypatch.setattr(infer_mod, "TfliteRunner", lambda _path: StubRunner([probs(lb=0.93)]))
    result = runner_cli.invoke(
        app,
        ["infer", "files", "--model", str(model), "--wav", str(tmp_path / "misc"),
         "--thresholds", str(thresholds_path)],
    )
    assert result.exit_code == 0, result.output
    assert "not labeled" in result.output
def test_cli_no_agreement_flag_skips_the_checkpoint(tmp_path, monkeypatch):
    write_wav(tmp_path / "lb" / "a.wav", tone(800.0, 0.3))
    labels_path = tmp_path / "build" / "labels.json"
    labels_path.parent.mkdir()
    labels_path.write_text(json.dumps(LABELS), encoding="utf-8")
    model = dummy_model(tmp_path / "build" / "model_int8.tflite")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(json.dumps(thresholds_doc()), encoding="utf-8")
    monkeypatch.setattr(infer_mod, "TfliteRunner", lambda _path: StubRunner([probs(lb=0.93)]))
    result = runner_cli.invoke(
        app,
        [
            "infer", "files",
            "--model", str(model),
            "--wav", str(tmp_path / "lb"),
            "--thresholds", str(thresholds_path),
            "--no-agreement",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--no-agreement" in result.output


def test_cli_missing_checkpoint_for_a_labeled_folder_is_loud(tmp_path, monkeypatch):
    write_wav(tmp_path / "lb" / "a.wav", tone(800.0, 0.3))
    labels_path = tmp_path / "build" / "labels.json"
    labels_path.parent.mkdir()
    labels_path.write_text(json.dumps(LABELS), encoding="utf-8")
    model = dummy_model(tmp_path / "build" / "model_int8.tflite")
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(
        json.dumps(thresholds_doc(commands={"lb": {"threshold": 0.80, "margin": 0.10}})),
        encoding="utf-8",
    )
    monkeypatch.setattr(infer_mod, "TfliteRunner", lambda _path: StubRunner([probs(lb=0.93)]))
    result = runner_cli.invoke(
        app,
        [
            "infer", "files",
            "--model", str(model),
            "--wav", str(tmp_path / "lb"),
            "--thresholds", str(thresholds_path),
            "--checkpoint", str(tmp_path / "nowhere.pt"),
            "--agreement-report", str(tmp_path / "build" / "out.md"),
        ],
    )
    assert result.exit_code == 2
    assert "ultron train run" in result.output
    assert not (tmp_path / "build" / "out.md").exists()


# ── End-to-end with real artifacts (heavy stack — requires local run) ───────


def _build_real_artifacts(tmp_path: Path) -> tuple[Path, Path, Path]:
    """T9 gold path on tone fixtures: features → train → sweep → export."""
    from ultron_model.export import read_labels_json

    dataset = tmp_path / "dataset"
    for label, freq in {"wake": 400.0, "noise": 3000.0, "lb": 800.0, "la": 1600.0}.items():
        for i in range(10):
            write_wav(dataset / "originals" / label / f"{label}_xiao_{i:02d}.wav", tone(freq + 3 * i))
        for i in range(3):
            write_wav(dataset / "holdout" / label / f"{label}_xiao_h{i}.wav", tone(freq + 50 + 3 * i))
    build = tmp_path / "build"
    steps = [
        ["features", "--dataset", str(dataset), "--out", str(build / "features.npz")],
        [
            "train", "run",
            "--archive", str(build / "features.npz"),
            "--holdout", str(dataset),
            "--out-checkpoint", str(build / "checkpoint.pt"),
            "--epochs", "300",
            "--min-originals", "0",
        ],
        [
            "train", "sweep",
            "--checkpoint", str(build / "checkpoint.pt"),
            "--holdout", str(dataset),
            "--out", str(build / "thresholds.json"),
            "--out-report", str(build / "sweep_report.json"),
        ],
        [
            "export", "tflite",
            "--checkpoint", str(build / "checkpoint.pt"),
            "--features", str(build / "features.npz"),
            "--out", str(build),
        ],
    ]
    for step in steps:
        result = runner_cli.invoke(app, step)
        assert result.exit_code == 0, f"{step} failed:\n{result.output}"
    assert read_labels_json(build / "labels.json") == LABELS
    return dataset, build, build / "thresholds.json"


@requires_conversion
def test_infer_gold_path_on_both_artifacts(tmp_path):
    """US-009 AC1/AC2: score a labeled folder through int8 AND fp16 (FR-034/036)."""
    dataset, build, thresholds_path = _build_real_artifacts(tmp_path)
    holdout = dataset / "holdout"
    summaries = {}
    for name in ("model_int8.tflite", "model_fp16.tflite"):
        report = build / f"agreement_{name}.md"
        result = runner_cli.invoke(
            app,
            [
                "infer", "files",
                "--model", str(build / name),
                "--wav", str(holdout),
                "--thresholds", str(thresholds_path),
                "--checkpoint", str(build / "checkpoint.pt"),
                "--agreement-report", str(report),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "accepted:" in result.output  # FR-034: scores + detections printed
        assert "window(s), " in result.output
        text = report.read_text()
        assert "host-TFLite vs PyTorch argmax agreement" in text
        assert "Mismatches (first" in text
        agreement = float(
            text.split("host-TFLite vs PyTorch argmax agreement: ")[1].split("%")[0]
        )
        assert agreement >= 99.0
        summaries[name] = (agreement, text.split("over ")[1].split(" ")[0])
    # both artifacts agree with PyTorch on the same windows
    assert summaries["model_int8.tflite"][1] == summaries["model_fp16.tflite"][1]


@requires_conversion
def test_infer_rejects_a_corrupted_real_artifact(tmp_path):
    """FR-034 on a real export: flip the flatbuffer identifier ⇒ loud failure (exit 2)."""
    dataset, build, thresholds_path = _build_real_artifacts(tmp_path)
    data = (build / "model_int8.tflite").read_bytes()
    corrupt = build / "corrupt.tflite"
    corrupt.write_bytes(data[:4] + b"XXXX" + data[8:])
    result = runner_cli.invoke(
        app,
        [
            "infer", "files",
            "--model", str(corrupt),
            "--wav", str(dataset / "holdout"),
            "--thresholds", str(thresholds_path),
            "--no-agreement",
        ],
    )
    assert result.exit_code == 2
    assert "not a TFLite flatbuffer" in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(
    os.environ.get("ULTRON_MIC_E2E") != "1",
    reason="live host microphone — REQUIRES LOCAL RUN "
    "(ULTRON_MIC_E2E=1 uv run pytest tests/test_infer.py -k mic_e2e)",
)
@requires_runtime
def test_infer_mic_e2e_requires_local_run(tmp_path):
    """FR-035 demo on real hardware: `ultron infer mic` scores live audio."""
    dataset, build, thresholds_path = _build_real_artifacts(tmp_path)
    result = runner_cli.invoke(
        app,
        [
            "infer", "mic",
            "--model", str(build / "model_int8.tflite"),
            "--thresholds", str(thresholds_path),
            "--seconds", "3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "scored" in result.output and "window(s)" in result.output
