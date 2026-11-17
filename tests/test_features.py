"""Feature extraction → feature archive — T7 (spec 002-can-think).

`ultron features` builds build/features.npz from the shared C MFCC binding.
These tests run entirely on generated fixtures and `ultron synth` (mock
engine) output — they never touch the real dataset/ (gitignored, absent in
CI worktrees) and never modify anything outside tmp_path.

Fixture durations are chosen to hit every windowing path:
  2.5 s → 2 windows (0 s, 0.5 s)      3.2 s → 3 windows (0 s, 0.5 s, 1.0 s)
  2.0 s → 1 window (exact fit)        <2 s  → 1 zero-padded window
"""

from __future__ import annotations

import hashlib
import shutil
import wave
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

import ultron.features as features_mod
from ultron import synth
from ultron.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SAMPLE_RATE = 16000
WINDOW_FRAMES = 100
NUM_COEFFS = 13

runner = CliRunner()


# ---- fixture helpers -------------------------------------------------------


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.astype("<i2").tobytes())


def tone(seed: int, seconds: float) -> np.ndarray:
    """Deterministic int16 clip (seeded): two sines + noise, full scale."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    sig = (
        0.4 * np.sin(2 * np.pi * (180 + seed * 37) * t)
        + 0.3 * np.sin(2 * np.pi * (900 + seed * 11) * t)
        + 0.2 * rng.standard_normal(t.size)
    )
    return (np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16)


def build_fixture_dataset(root: Path) -> None:
    """Initial 4-class set (wake/noise/la/lb — TD-012) + policy edges."""
    write_wav(root / "originals/wake/wake_ext_01.wav", tone(1, 2.5))  # 2 rows
    write_wav(root / "originals/wake/wake_ext_02.wav", tone(2, 0.8))  # 1 padded row
    write_wav(root / "originals/noise/noise_ext_01.wav", tone(3, 3.2))  # 3 rows
    write_wav(root / "originals/la/la_ext_01.wav", tone(4, 2.0))  # 1 exact row
    write_wav(root / "originals/lb/lb_ext_01.wav", tone(5, 1.5))  # 1 padded row
    # augmented variants join the training set (spec 001: seeded augmentation)
    write_wav(root / "augmented/la/aug_la_ext_01_gain.wav", tone(6, 1.2))  # 1 padded
    # holdout is structurally excluded (NFR-003 — isolation by location)
    write_wav(root / "holdout/lb/lb_ext_99.wav", tone(7, 2.2))
    # synthetic enters only via --with-synthetic (spec 001 policy)
    write_wav(root / "synthetic/wake/syn_wake_mock_1.0_01.wav", tone(8, 1.0))


def run_features(dataset: Path, out: Path, *extra: str):
    return runner.invoke(
        app, ["features", "--dataset", str(dataset), "--out", str(out), *extra]
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    build_fixture_dataset(root)
    return root


# ---- the archive contract --------------------------------------------------


def test_features_builds_archive(dataset: Path, tmp_path: Path) -> None:
    out = tmp_path / "build" / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 0, result.output

    assert out.is_file()
    with np.load(out) as arch:
        assert set(arch.files) == {"features", "labels", "paths", "split_hash"}

        feats = arch["features"]
        assert feats.dtype == np.float32
        assert feats.shape == (9, WINDOW_FRAMES, NUM_COEFFS)
        assert not np.isnan(feats).any()

        labels = arch["labels"].tolist()
        paths = arch["paths"].tolist()
        assert len(labels) == len(paths) == 9

        # per-row label matches the label directory of the source path
        for label, path in zip(labels, paths):
            assert label == path.split("/")[1]

        # originals + augmented only: holdout/takes/synthetic never appear
        assert all(p.startswith(("originals/", "augmented/")) for p in paths)
        assert not any("holdout" in p or "synthetic" in p for p in paths)

        assert len(arch["split_hash"].item()) == 64  # sha256 hex

    # rows per label: wake 2+1 (2.5 s + padded), noise 3 (3.2 s),
    # la 1+1 (exact + augmented), lb 1 (padded)
    assert Counter(labels) == {"wake": 3, "noise": 3, "la": 2, "lb": 1}


def test_windowing_and_padding(dataset: Path, tmp_path: Path) -> None:
    """2.5 s → 2 rows, 3.2 s → 3 rows, exact 2 s → 1 row, short → 1 padded row."""
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 0, result.output
    with np.load(out) as arch:
        counts = Counter(arch["paths"].tolist())
    assert counts["originals/wake/wake_ext_01.wav"] == 2  # 2.5 s, 500 ms hop
    assert counts["originals/noise/noise_ext_01.wav"] == 3  # 3.2 s
    assert counts["originals/la/la_ext_01.wav"] == 1  # exact 2 s fit
    assert counts["originals/wake/wake_ext_02.wav"] == 1  # 0.8 s zero-padded
    assert counts["originals/lb/lb_ext_01.wav"] == 1  # 1.5 s zero-padded
    assert counts["augmented/la/aug_la_ext_01_gain.wav"] == 1


def test_rerun_is_byte_identical(dataset: Path, tmp_path: Path) -> None:
    """T7 verify gate: re-running over the same tree ⇒ identical file hash."""
    out = tmp_path / "features.npz"
    assert run_features(dataset, out).exit_code == 0
    first = sha256_file(out)
    assert run_features(dataset, out).exit_code == 0
    assert sha256_file(out) == first

    # a fresh output path is byte-identical too (no embedded timestamps)
    out2 = tmp_path / "features_again.npz"
    assert run_features(dataset, out2).exit_code == 0
    assert sha256_file(out2) == first


def test_synthetic_only_with_flag(dataset: Path, tmp_path: Path) -> None:
    without = tmp_path / "without.npz"
    assert run_features(dataset, without).exit_code == 0
    with np.load(without) as arch:
        assert not any(p.startswith("synthetic/") for p in arch["paths"].tolist())

    with_flag = tmp_path / "with.npz"
    result = run_features(dataset, with_flag, "--with-synthetic")
    assert result.exit_code == 0, result.output
    with np.load(with_flag) as arch:
        paths = arch["paths"].tolist()
    assert "synthetic/wake/syn_wake_mock_1.0_01.wav" in paths
    assert len(paths) == 10


def test_split_hash_tracks_the_row_manifest(dataset: Path, tmp_path: Path) -> None:
    """Same tree ⇒ same split hash; a changed tree ⇒ different hash."""
    out_a = tmp_path / "a.npz"
    out_b = tmp_path / "b.npz"
    assert run_features(dataset, out_a).exit_code == 0
    assert run_features(dataset, out_b).exit_code == 0
    with np.load(out_a) as a, np.load(out_b) as b:
        assert a["split_hash"].item() == b["split_hash"].item()

    write_wav(dataset / "originals/lb/lb_ext_02.wav", tone(9, 2.1))
    out_c = tmp_path / "c.npz"
    assert run_features(dataset, out_c).exit_code == 0
    with np.load(out_a) as a, np.load(out_c) as c:
        assert c["split_hash"].item() != a["split_hash"].item()


def test_holdout_never_enters_the_archive(dataset: Path, tmp_path: Path) -> None:
    for extra in ([], ["--with-synthetic"]):
        out = tmp_path / f"h{len(extra)}.npz"
        assert run_features(dataset, out, *extra).exit_code == 0
        with np.load(out) as arch:
            assert not any("holdout" in p for p in arch["paths"].tolist())


# ---- loud failures (FR-022: no silent training on corrupt data) ------------


def test_nan_feature_row_fails_loudly(dataset: Path, tmp_path: Path, monkeypatch) -> None:
    """A NaN row (silence-padded frames of the 0.8 s clip) names its file."""

    class PaddedNaNMfcc:
        def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
            pass

        def compute(self, frame):
            if not frame.any():  # zero-padded tail of a short clip
                return [float("nan")] * NUM_COEFFS
            return [0.0] * NUM_COEFFS

        def close(self) -> None:
            pass

    monkeypatch.setattr(features_mod, "Mfcc", PaddedNaNMfcc)
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 1
    assert "NaN" in result.output
    assert "wake_ext_02.wav" in result.output
    assert "FR-022" in result.output
    assert not out.exists()  # nothing written on failure


def test_corrupt_wav_fails_loudly(dataset: Path, tmp_path: Path) -> None:
    shutil.copy(
        FIXTURES / "corrupt" / "8bit.wav", dataset / "originals/la/la_bad_01.wav"
    )
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 1
    assert "la_bad_01.wav" in result.output
    assert "16000Hz/16bit/1ch" in result.output
    assert not out.exists()


def test_unknown_label_dir_fails_loudly(dataset: Path, tmp_path: Path) -> None:
    write_wav(dataset / "originals/unknown_label/x_01.wav", tone(10, 2.0))
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 1
    assert "unknown_label" in result.output
    assert "valid labels" in result.output


def test_synthetic_clip_outside_synthetic_dir_fails_loudly(
    dataset: Path, tmp_path: Path
) -> None:
    write_wav(dataset / "originals/noise/syn_noise_01.wav", tone(11, 2.0))
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 1
    assert "syn_noise_01.wav" in result.output


def test_unlabeled_clip_at_source_root_fails_loudly(
    dataset: Path, tmp_path: Path
) -> None:
    write_wav(dataset / "originals/stray.wav", tone(12, 2.0))
    out = tmp_path / "features.npz"
    result = run_features(dataset, out)
    assert result.exit_code == 1
    assert "stray.wav" in result.output


def test_nested_label_dirs_are_included(dataset: Path, tmp_path: Path) -> None:
    """synth noise nests by source (synthetic/noise/car/...) — still label=noise."""
    write_wav(dataset / "synthetic/noise/car/syn_noise_car_01.wav", tone(13, 2.0))
    write_wav(dataset / "synthetic/noise/room/syn_noise_room_01.wav", tone(14, 2.0))
    out = tmp_path / "features.npz"
    result = run_features(dataset, out, "--with-synthetic")
    assert result.exit_code == 0, result.output
    with np.load(out) as arch:
        pairs = set(zip(arch["paths"].tolist(), arch["labels"].tolist()))
    assert ("synthetic/noise/car/syn_noise_car_01.wav", "noise") in pairs
    assert ("synthetic/noise/room/syn_noise_room_01.wav", "noise") in pairs


def test_empty_dataset_fails_loudly(tmp_path: Path) -> None:
    empty = tmp_path / "dataset"
    empty.mkdir()
    result = run_features(empty, tmp_path / "features.npz")
    assert result.exit_code == 1
    assert "no wav clips" in result.output


# ---- integration with `ultron synth` output (mock engine, offline) ---------


def test_features_over_synth_mock_output(tmp_path: Path) -> None:
    """The documented fixture path: `ultron synth tts` (mock) → `ultron features`."""
    dataset = tmp_path / "dataset"
    rendered = synth.render_tts(
        voices=("Mónica",),
        speeds=("1.0",),
        output=dataset / "synthetic",
        engine="mock",
        registry_path=REPO_ROOT / "model" / "registry.json",
    )
    assert sum(rendered.values()) > 0

    out = tmp_path / "features.npz"
    result = run_features(dataset, out, "--with-synthetic")
    assert result.exit_code == 0, result.output
    with np.load(out) as arch:
        paths = arch["paths"].tolist()
        labels = arch["labels"].tolist()
        feats = arch["features"]

    # one row per mock clip (all ≤ 2 s → single padded window)
    assert len(paths) == sum(rendered.values())
    assert all(p.startswith("synthetic/") for p in paths)
    assert sorted(set(labels)) == sorted(rendered.keys())
    assert feats.dtype == np.float32
    assert not np.isnan(feats).any()
