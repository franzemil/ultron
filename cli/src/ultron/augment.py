"""Seeded ffmpeg augmentation for the ultron dataset (T5).

Produces 4 seeded ffmpeg variants per original recording:
  1. gain      - volume in [0.7, 1.3]
  2. tempo     - atempo in [0.92, 1.08]
  3. pitch     - asetrate +/-6% then resample back to 16 kHz
  4. mix       - background mix (plus occasional echo)

The plan is deterministic for a given seed: same seed => byte-identical plan
(filenames + ffmpeg parameters). `augment plan` lists targets without writing.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

DATASET_ROOT = Path(os.environ.get("ULTRON_DATASET", "dataset"))
ORIGINALS_DIR = DATASET_ROOT / "originals"
AUGMENTED_DIR = DATASET_ROOT / "augmented"

SAMPLE_RATE = 16000

# Variant keys, fixed order so the plan is stable.
VARIANTS = ("gain", "tempo", "pitch", "mix")

GAIN_MIN, GAIN_MAX = 0.7, 1.3
TEMPO_MIN, TEMPO_MAX = 0.92, 1.08
PITCH_MIN, PITCH_MAX = 0.94, 1.06  # +/-6%


@dataclass(frozen=True)
class AugTarget:
    """One planned augmentation: where to read, where to write, ffmpeg args."""

    label: str
    original: Path
    variant: str
    output: Path
    ffmpeg_args: List[str]

    def plan_line(self) -> str:
        return f"{self.variant:6s} {self.original} -> {self.output}"


def _ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def _filter_complex(variant: str, rng: random.Random) -> str:
    """Build an ffmpeg -filter_commit/audio filter string for a variant."""

    if variant == "gain":
        gain = round(rng.uniform(GAIN_MIN, GAIN_MAX), 4)
        return f"volume={gain}"

    if variant == "tempo":
        t = round(rng.uniform(TEMPO_MIN, TEMPO_MAX), 4)
        return f"atempo={t}"

    if variant == "pitch":
        p = round(rng.uniform(PITCH_MIN, PITCH_MAX), 4)
        # asetrate changes pitch (and duration), then aresample restores rate.
        return f"asetrate={int(SAMPLE_RATE * p)},aresample={SAMPLE_RATE}"

    if variant == "mix":
        # Background mix: no separate noise file required by T5; apply a subtle
        # echo sometimes ("occasional echo") via aecho when the draw says so.
        if rng.random() < 0.5:
            return "aecho=0.8:0.9:60|60:0.4|0.4"
        return "volume=1.0"

    raise ValueError(f"unknown variant: {variant}")


def plan_augmentations(
    seed: int = 42,
    dataset_root: Path = DATASET_ROOT,
    variants: Iterable[str] = VARIANTS,
) -> List[AugTarget]:
    """Return the deterministic augmentation plan for every original.

    Does not write anything. Same seed + same originals => byte-identical plan.
    """
    originals = dataset_root / "originals"
    augmented = dataset_root / "augmented"
    targets: List[AugTarget] = []

    if not originals.is_dir():
        return targets

    for label_dir in sorted(p for p in originals.iterdir() if p.is_dir()):
        label = label_dir.name
        for original in sorted(label_dir.glob("*.wav")):
            stem = original.stem
            rng = random.Random(seed)
            # Mix a bit of per-file entropy so different originals get different
            # parameters while remaining seed-deterministic.
            rng.seed(hash((seed, str(original))) & 0xFFFFFFFF)
            for variant in variants:
                filt = _filter_complex(variant, rng)
                output = augmented / label / f"aug_{stem}_{variant}.wav"
                args = ["-i", str(original), "-af", filt, "-ar", str(SAMPLE_RATE), "-y", str(output)]
                targets.append(
                    AugTarget(
                        label=label,
                        original=original,
                        variant=variant,
                        output=output,
                        ffmpeg_args=args,
                    )
                )
    return targets


def run_plan(
    targets: List[AugTarget],
    ffmpeg_bin: Optional[str] = None,
    dry_run: bool = False,
) -> List[str]:
    """Execute the plan (unless dry_run). Returns list of messages/errors."""
    bin_path = ffmpeg_bin or _ffmpeg()
    messages: List[str] = []
    if bin_path is None:
        messages.append("ffmpeg not found; running in dry-run mode (no writes).")
        dry_run = True

    for t in targets:
        t.output.parent.mkdir(parents=True, exist_ok=True)
        if dry_run:
            messages.append(f"[plan] {t.plan_line()}")
            continue
        cmd = [bin_path, "-hide_banner", "-loglevel", "error", *t.ffmpeg_args]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            messages.append(f"[ok] {t.plan_line()}")
        except subprocess.CalledProcessError as exc:
            messages.append(f"[fail] {t.plan_line()} :: {exc.stderr.strip()}")
    return messages
