"""Tests for T5 seeded augmentation.

Verifies: same seed => byte-identical plan; `augment plan` lists targets without writing.
ffmpeg is exercised only when installed (it is on this host); when absent, the plan
logic and determinism are still verified via a dry-run path.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from ultron.augment import (
    AugTarget,
    DATASET_ROOT,
    ORIGINALS_DIR,
    plan_augmentations,
    run_plan,
)


def _write_wav(path: Path, seconds: float = 0.5, rate: int = 16000) -> None:
    """Write a tiny valid 16-bit/mono WAV (silent sine) so ffmpeg can consume it."""
    import math
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(rate * seconds)
    frames = b"".join(
        struct.pack("<h", int(0.3 * 3000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(n)
    )
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVE"
    header += b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(frames))
    path.write_bytes(header + frames)


def _plan_hash(targets) -> str:
    h = hashlib.sha256()
    for t in targets:
        h.update(
            f"{t.label}|{t.original}|{t.variant}|{t.output}|{' '.join(t.ffmpeg_args)}".encode()
        )
    return h.hexdigest()


@pytest.fixture
def originals_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the dataset at a temp dir with a couple of original WAVs."""
    root = tmp_path / "dataset"
    orig = root / "originals"
    labels = ["lm", "gd"]
    for label in labels:
        for i in range(2):
            _write_wav(orig / label / f"{label}_{i:02d}.wav")
    monkeypatch.setenv("ULTRON_DATASET", str(root))
    return orig


def test_plan_is_deterministic(originals_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same seed => byte-identical plan (the core T5 requirement)."""
    root = originals_dir.parent
    t1 = plan_augmentations(seed=42, dataset_root=root)
    t2 = plan_augmentations(seed=42, dataset_root=root)
    assert _plan_hash(t1) == _plan_hash(t2)

    # Different seed => different plan (parameters differ).
    t3 = plan_augmentations(seed=7, dataset_root=root)
    assert _plan_hash(t1) != _plan_hash(t3)


def test_four_variants_per_original(originals_dir: Path) -> None:
    """Exactly 4 variants per original file."""
    root = originals_dir.parent
    targets = plan_augmentations(seed=42, dataset_root=root)
    # 2 labels x 2 originals x 4 variants = 16
    assert len(targets) == 16
    by_variant = {}
    for t in targets:
        by_variant.setdefault(t.variant, 0)
        by_variant[t.variant] += 1
    assert by_variant == {"gain": 4, "tempo": 4, "pitch": 4, "mix": 4}


def test_deterministic_filenames(originals_dir: Path) -> None:
    """Filenames are deterministic functions of original + variant."""
    root = originals_dir.parent
    targets = plan_augmentations(seed=42, dataset_root=root)
    for t in targets:
        assert t.output.name.startswith("aug_")
        assert t.output.name.endswith(f"_{t.variant}.wav")
        assert t.output.parent.name == t.label
        assert t.output.parent.parent.name == "augmented"


def test_plan_writes_nothing(originals_dir: Path, tmp_path: Path) -> None:
    """`augment plan` (plan_augmentations) creates no files on disk."""
    root = originals_dir.parent
    augmented = root / "augmented"
    plan_augmentations(seed=42, dataset_root=root)
    assert not augmented.exists()


def test_plan_lists_targets(originals_dir: Path, capsys) -> None:
    """plan_augmentations returns AugTarget objects describing each target."""
    root = originals_dir.parent
    targets = plan_augmentations(seed=42, dataset_root=root)
    assert isinstance(targets, list) and all(isinstance(t, AugTarget) for t in targets)
    assert len(targets) > 0
    # Every target names a real original and an output path.
    for t in targets:
        assert t.original.exists()
        assert str(t.output).endswith(".wav")


def test_ffmpeg_not_installed_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ffmpeg is absent, run_plan degrades to dry-run messages."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    targets = [
        AugTarget(
            label="lm",
            original=Path("dataset/originals/lm/lm_00.wav"),
            variant="gain",
            output=Path("dataset/augmented/lm/aug_lm_00_gain.wav"),
            ffmpeg_args=["-i", "in.wav", "-af", "volume=1.0", "-ar", "16000", "-y", "out.wav"],
        )
    ]
    msgs = run_plan(targets, dry_run=False)
    assert any("ffmpeg not found" in m for m in msgs)


@pytest.mark.skipif(
    subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0,
    reason="ffmpeg not installed",
)
def test_ffmpeg_run_writes_variants(originals_dir: Path, tmp_path: Path) -> None:
    """When ffmpeg is present, run_plan writes real augmented WAVs."""
    root = originals_dir.parent
    targets = plan_augmentations(seed=42, dataset_root=root)
    msgs = run_plan(targets, dry_run=False)
    written = list((root / "augmented").rglob("*.wav"))
    assert len(written) == 16, f"expected 16 written, got {len(written)}: {msgs}"
    assert all("[ok]" in m for m in msgs), msgs
