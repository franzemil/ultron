"""T9 export tests — ONNX → TFLite + parity gate (spec 002-can-think).

Order mirrors the task checklist: the **conversion smoke test on a randomly
initialized model comes first** (design.md's onnx2tf risk mitigation), then the
parity-gate logic, labels-order check and size reporting — all of which are
pure NumPy/stdlib and always run. The end-to-end conversion needs the heavy
export stack and is skipped with an explicit reason when it is absent
(`uv sync --extra export`) — it requires a local run.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from ultron_model.export import (
    INT8_FLASH_BUDGET_BYTES,
    PARITY_THRESHOLD,
    LabelsOrderError,
    ParityGateError,
    argmax_agreement,
    budget_status,
    check_archive_labels,
    check_labels_order,
    conversion_deps_available,
    dependency_versions,
    export_tflite,
    format_bytes,
    parity_gate,
    read_labels_json,
    render_parity_report,
    representative_dataset,
    sample_indices,
    write_labels_json,
)
from ultron_model.export import ExportResult  # for report rendering fixtures

# T8's frozen class order for the initial 4-class set (wake, noise + registry
# command order — derive_class_set, FR-032/TD-012; NOT alphabetical)
LABELS = ["wake", "noise", "lb", "la"]

requires_conversion = pytest.mark.skipif(
    not conversion_deps_available(),
    reason="conversion stack (torch/onnx2tf/tensorflow) not installed — "
    "REQUIRES LOCAL RUN: `uv sync --extra export`",
)


def _t8_checkpoint(model, classes):
    """A minimal T8-format checkpoint payload (format 1)."""
    return {
        "format": 1,
        "model_state": {k: v.detach().clone() for k, v in model.state_dict().items()},
        "classes": list(classes),
        "in_frames": 100,
        "in_coeffs": 13,
        "seed": 42,
        "split_hash": "test",
        "split_manifest_hash": "test",
        "torch_version": "test",
    }


def _write_archive(path, n, classes, seed=42):
    """A minimal T7-format feature archive (features/labels/paths/split_hash)."""
    rng = np.random.default_rng(seed)
    np.savez(
        path,
        features=rng.standard_normal((n, 100, 13)).astype(np.float32),
        labels=rng.choice(classes, size=n),
        paths=np.array([f"originals/{c}/{c}_xiao_00.wav" for c in rng.choice(classes, size=n)]),
        split_hash="test",
    )


# ── T9 checklist item 1: conversion smoke on a randomly initialized model ────


@requires_conversion
def test_conversion_smoke_random_model(tmp_path):
    """Risk mitigation: prove the ONNX → onnx2tf → int8 path on random weights.

    Uses T8's real ``KwsNet`` architecture with a fixed seed and spread logits
    so argmax is robust to int8 rounding; asserts all artifacts, the frozen
    label order and the ≥ 99% gate.
    """
    from ultron_model.export import reference_model

    rng = np.random.default_rng(42)
    features = rng.standard_normal((64, 100, 13)).astype(np.float32)
    model = reference_model(len(LABELS), seed=42, logit_scale=8.0)

    result = export_tflite(model, LABELS, features, tmp_path, max_calibration=16, max_parity=64)

    assert (tmp_path / "model.onnx").is_file()
    assert (tmp_path / "model_int8.tflite").is_file()
    assert (tmp_path / "labels.json").is_file()
    assert (tmp_path / "parity_report.md").is_file()

    assert result.labels == LABELS
    assert read_labels_json(tmp_path / "labels.json") == LABELS
    assert result.gate_passed
    assert result.int8_agreement >= PARITY_THRESHOLD
    assert result.versions["torch"] != "not installed"

    # this architecture is tiny by design — well inside the NFR-002 flash budget
    assert result.int8_within_budget, result.int8_bytes


# ── Parity gate (FR-011 / NFR-007) ───────────────────────────────────────────


def test_parity_gate_passes_at_threshold():
    parity_gate(PARITY_THRESHOLD)  # exactly 99% passes (gate is ≥ 99%)
    parity_gate(1.0)


def test_parity_gate_fails_below_threshold():
    with pytest.raises(ParityGateError, match="99"):
        parity_gate(0.9899)


def test_corrupted_parity_fixture_fails_gate():
    """Deliberately corrupted parity (2% argmax mismatches) MUST fail the gate."""
    rng = np.random.default_rng(7)
    reference = rng.standard_normal((100, len(LABELS))).astype(np.float32)
    corrupted = reference.copy()
    flip = rng.choice(len(corrupted), size=2, replace=False)  # 2/100 = 98% agreement
    for i in flip:
        arg = int(reference[i].argmax())
        corrupted[i, arg] = reference[i, arg] - 1.0  # a different class now wins
        assert corrupted[i].argmax() != arg

    agreement = argmax_agreement(reference, corrupted)
    assert agreement == pytest.approx(0.98)
    with pytest.raises(ParityGateError, match="98.00%"):
        parity_gate(agreement)


def test_argmax_agreement_full_match():
    scores = np.array([[0.1, 0.9], [0.8, 0.2], [0.3, 0.7]])
    assert argmax_agreement(scores, scores + 0.01) == 1.0


def test_argmax_agreement_rejects_shape_mismatch():
    scores = np.zeros((4, 3))
    with pytest.raises(Exception, match="shape"):
        argmax_agreement(scores, np.zeros((4, 2)))


# ── Frozen class order + build/labels.json order check ───────────────────────


def test_labels_json_round_trip_and_order_check(tmp_path):
    path = write_labels_json(LABELS, tmp_path / "labels.json")
    assert read_labels_json(path) == LABELS
    check_labels_order(LABELS, read_labels_json(path), "test")  # OK, no raise


def test_labels_json_order_check_detects_corruption(tmp_path):
    """A reordered labels.json must fail the order check naming both orders."""
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(sorted(LABELS)))  # alphabetical ≠ frozen order
    with pytest.raises(LabelsOrderError, match="class order mismatch"):
        check_labels_order(LABELS, read_labels_json(path), "build/labels.json")


def test_check_labels_order_rejects_subset():
    with pytest.raises(LabelsOrderError, match="expected"):
        check_labels_order(LABELS, LABELS[:3], "checkpoint vs feature archive")


def test_check_archive_labels_accepts_matching_set():
    check_archive_labels(["wake", "la", "noise", "lb", "la"], LABELS)  # no raise


def test_check_archive_labels_rejects_unknown_label():
    with pytest.raises(LabelsOrderError, match="not in the frozen class set.*'gd'"):
        check_archive_labels(["wake", "noise", "lb", "la", "gd"], LABELS)


def test_check_archive_labels_rejects_missing_class():
    with pytest.raises(LabelsOrderError, match="absent from the archive.*'la'"):
        check_archive_labels(["wake", "noise", "lb"], LABELS)


# ── Sizes vs the NFR-002 budget (spec 003) ───────────────────────────────────


def test_size_report_vs_nfr002_budget():
    assert budget_status(5 * 1024, INT8_FLASH_BUDGET_BYTES) == "PASS"
    assert budget_status(100 * 1024, INT8_FLASH_BUDGET_BYTES) == "PASS"  # at budget
    assert budget_status(100 * 1024 + 1, INT8_FLASH_BUDGET_BYTES) == "OVER BUDGET"


def test_format_bytes():
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(9232) == "9.0 KB"


def test_report_renders_gate_labels_sizes_and_parity(tmp_path):
    result = ExportResult(
        out_dir=tmp_path,
        labels=LABELS,
        onnx_path=tmp_path / "model.onnx",
        int8_path=tmp_path / "model_int8.tflite",
        labels_path=tmp_path / "labels.json",
        report_path=tmp_path / "parity_report.md",
        onnx_bytes=12_586,
        int8_bytes=9_232,
        int8_agreement=0.9975,
        n_parity=400,
        threshold=PARITY_THRESHOLD,
        versions={"python": "3.12.9", "torch": "2.15.0"},
    )
    report = render_parity_report(result)
    assert "**Gate: PASS**" in report
    assert ", ".join(LABELS) in report
    assert "order check: OK" in report
    assert "9.0 KB" in report and "≤ 100.0 KB" in report and "PASS" in report
    assert "99.75%" in report
    assert "torch" in report
    assert result.gate_passed and result.int8_within_budget


def test_report_renders_fail_verdict_on_gate_miss(tmp_path):
    result = ExportResult(
        out_dir=tmp_path,
        labels=LABELS,
        onnx_path=tmp_path / "model.onnx",
        int8_path=tmp_path / "model_int8.tflite",
        labels_path=tmp_path / "labels.json",
        report_path=tmp_path / "parity_report.md",
        onnx_bytes=12_586,
        int8_bytes=9_232,
        int8_agreement=0.98,
        n_parity=100,
        threshold=PARITY_THRESHOLD,
        versions=dependency_versions(),
    )
    report = render_parity_report(result)
    assert "**Gate: FAIL**" in report
    assert "98.00%" in report
    assert not result.gate_passed


# ── Representative dataset ───────────────────────────────────────────────────


def test_representative_dataset_deterministic_shapes():
    features = np.arange(5 * 100 * 13, dtype=np.float32).reshape(5, 100, 13)
    gen = representative_dataset(features, (1, 100, 13), max_samples=3)
    batches = list(gen())
    assert len(batches) == 3
    for [sample] in batches:
        assert sample.shape == (1, 100, 13)
        assert sample.dtype == np.float32
    # deterministic: same call → same samples
    np.testing.assert_array_equal([s for [s] in list(gen())], [s for [s] in batches])


def test_sample_indices_coverage_capped():
    assert list(sample_indices(10, 100)) == list(range(10))
    idx = sample_indices(1000, 10)
    assert len(idx) == 10 and idx[0] == 0 and idx[-1] == 999


# ── CLI wiring ───────────────────────────────────────────────────────────────


def test_cli_export_help():
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "tflite" in result.output


def test_cli_export_missing_files_fail_cleanly(tmp_path):
    """Missing checkpoint/archive exit 2 with a message — no traceback."""
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(
        app,
        [
            "export",
            "tflite",
            "--checkpoint",
            str(tmp_path / "nope.pt"),
            "--features",
            str(tmp_path / "nope.npz"),
        ],
    )
    assert result.exit_code == 2
    assert "not found" in result.output
    assert "Traceback" not in result.output


def test_cli_export_gate_failure_exits_one(tmp_path, monkeypatch):
    """A parity-gate failure surfaces as exit code 1 with the report path."""
    import ultron.export as cli_export
    from typer.testing import CliRunner

    from ultron.cli import app

    def _failing_run_export(*args, **kwargs):
        raise ParityGateError(
            "parity gate FAILED: PyTorch vs int8 argmax agreement 98.00% < 99.00% "
            "(FR-011 / NFR-007) — export rejected"
        )

    monkeypatch.setattr(cli_export, "run_export", _failing_run_export)
    result = CliRunner().invoke(app, ["export", "tflite"])
    assert result.exit_code == 1
    assert "parity gate FAILED" in result.output
    assert "parity_report.md" in result.output


def test_cli_export_deps_missing_exit_three(tmp_path, monkeypatch):
    from ultron_model.export import ExportDepsError
    import ultron.export as cli_export
    from typer.testing import CliRunner

    from ultron.cli import app

    def _no_deps(*args, **kwargs):
        raise ExportDepsError("torch is required … `uv sync --extra export`")

    monkeypatch.setattr(cli_export, "run_export", _no_deps)
    (tmp_path / "checkpoint.pt").write_bytes(b"x")
    (tmp_path / "features.npz").write_bytes(b"x")
    result = CliRunner().invoke(
        app,
        [
            "export",
            "tflite",
            "--checkpoint",
            str(tmp_path / "checkpoint.pt"),
            "--features",
            str(tmp_path / "features.npz"),
        ],
    )
    assert result.exit_code == 3
    assert "uv sync --extra export" in result.output


# ── End-to-end through the real T8 artifacts (heavy stack) ───────────────────


@requires_conversion
class _StubRefresh:
    """Stand-in for esp32lib.refresh(): records the call, touches no files."""

    version_after = "0.0.0-stub"

    def summary_lines(self):
        return ["UltronVoice v0.0.0-stub (stubbed in tests)"]


@pytest.fixture
def stub_library_refresh(monkeypatch):
    """Export always refreshes the UltronVoice library; tests must not touch the repo copy.

    The stub also *asserts the contract*: every successful `ultron export tflite` calls
    esp32lib.refresh() exactly once with the int8 flatbuffer it just produced.
    """
    import ultron.esp32lib as esp32lib

    calls: list = []

    def _fake_refresh(*, model=None, placeholder=False, labels=None, bump=True):
        calls.append({"model": model, "bump": bump})
        return _StubRefresh()

    monkeypatch.setattr(esp32lib, "refresh", _fake_refresh)
    return calls


def test_cli_export_tflite_end_to_end(tmp_path, stub_library_refresh):
    """`ultron export tflite` on a T8-format checkpoint + T7-format archive."""
    import torch

    from ultron_model.export import reference_model

    model = reference_model(len(LABELS), seed=123, logit_scale=8.0)
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(_t8_checkpoint(model, LABELS), checkpoint)
    archive = tmp_path / "features.npz"
    _write_archive(archive, 48, LABELS, seed=123)

    from typer.testing import CliRunner

    from ultron.cli import app

    out = tmp_path / "build"
    result = CliRunner().invoke(
        app,
        [
            "export",
            "tflite",
            "--checkpoint",
            str(checkpoint),
            "--features",
            str(archive),
            "--out",
            str(out),
            "--max-calibration",
            "12",
            "--max-parity",
            "48",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "labels (frozen order)" in result.output
    assert "model_int8.tflite" in result.output
    assert (out / "model_int8.tflite").stat().st_size > 0
    report = (out / "parity_report.md").read_text()
    assert "**Gate: PASS**" in report
    assert read_labels_json(out / "labels.json") == LABELS
    # the library refresh is not optional: exactly one call, on this export's flatbuffer
    assert len(stub_library_refresh) == 1, stub_library_refresh
    assert stub_library_refresh[0]["model"] == out / "model_int8.tflite"


@requires_conversion
def test_export_fails_when_checkpoint_disagrees_with_archive(tmp_path):
    """Archive labels outside the frozen class set fail loudly."""
    import torch

    from ultron_model.export import reference_model, run_export

    model = reference_model(4, seed=5)
    checkpoint = tmp_path / "checkpoint.pt"
    torch.save(_t8_checkpoint(model, LABELS), checkpoint)
    # archive carries an untrained command → class sets disagree
    _write_archive(tmp_path / "features.npz", 16, ["wake", "noise", "lb", "gd"], seed=5)

    with pytest.raises(LabelsOrderError, match="gd"):
        run_export(checkpoint, tmp_path / "features.npz", tmp_path / "build")


@requires_conversion
def test_export_gold_path_trained_model(tmp_path, stub_library_refresh):
    """Full chain on real artifacts: fixtures → `ultron features` → `ultron
    train run` → `ultron export tflite` — the trained checkpoint passes the
    gate (T8's own smoke pattern, extended through T9)."""
    import wave

    from typer.testing import CliRunner

    from ultron.cli import app

    runner = CliRunner()
    sample_rate = 16000

    def tone(seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        t = np.arange(sample_rate // 2) / sample_rate
        sig = 0.4 * np.sin(2 * np.pi * (180 + seed * 37) * t) + 0.2 * rng.standard_normal(t.size)
        return (np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16)

    dataset = tmp_path / "dataset"
    seeds = {"wake": 1, "noise": 2, "lb": 3, "la": 4}
    for label, seed in seeds.items():
        for i in range(6):  # small — parity compares torch vs tflite, not accuracy
            wav = dataset / "originals" / label / f"{label}_xiao_{i:02d}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sample_rate)
                w.writeframes(tone(seed).tobytes())
    for label in seeds:  # fresh hold-out tree for `train run`
        for i in range(2):
            wav = dataset / "holdout" / label / f"{label}_xiao_h{i}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sample_rate)
                w.writeframes(tone(seeds[label] + 100 + i).tobytes())

    build = tmp_path / "build"
    feats = runner.invoke(app, ["features", "--dataset", str(dataset), "--out", str(build / "features.npz")])
    assert feats.exit_code == 0, feats.output

    trained = runner.invoke(
        app,
        [
            "train", "run",
            "--archive", str(build / "features.npz"),
            "--holdout", str(dataset),
            "--out-checkpoint", str(build / "checkpoint.pt"),
            "--epochs", "3",
            "--min-originals", "0",
        ],
    )
    assert trained.exit_code == 0, trained.output

    exported = runner.invoke(
        app,
        [
            "export", "tflite",
            "--checkpoint", str(build / "checkpoint.pt"),
            "--features", str(build / "features.npz"),
            "--out", str(build),
        ],
    )
    assert exported.exit_code == 0, exported.output
    assert read_labels_json(build / "labels.json") == LABELS
    assert "**Gate: PASS**" in (build / "parity_report.md").read_text()
    assert len(stub_library_refresh) == 1, stub_library_refresh


def test_export_produces_int8_only():
    """Single target: no float16 artifact, no Pi consumer (owner decision 2026-11-26)."""
    import dataclasses

    from ultron_model.export import ExportResult

    fields = {f.name for f in dataclasses.fields(ExportResult)}
    assert "int8_path" in fields and "int8_bytes" in fields
    assert not {f for f in fields if f.startswith("fp16")}
