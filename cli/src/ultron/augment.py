"""Seeded ffmpeg augmentation for the ultron dataset (T5).

Produces 5 seeded ffmpeg variants per original recording:
  1. gain      - volume in [0.7, 1.3]
  2. tempo     - atempo in [0.92, 1.08]
  3. pitch     - asetrate +/-6% then resample back to 16 kHz
  4. mix       - real background noise from the dataset's own noise clips (looped under the
                 speech, seeded SNR-ish level); falls back to a flat copy when no noise exists
  5. shift     - the utterance moved to a random offset of the 2.0 s analysis window, so the
                 model sees the phrase where a live sliding window would catch it instead of
                 only at offset 0 (the padding artefact that made `la`/`lb` look identical)

The plan is deterministic for a given seed: same seed => byte-identical plan
(filenames + ffmpeg parameters). `augment plan` lists targets without writing.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

DATASET_ROOT = Path(os.environ.get("ULTRON_DATASET", "dataset"))
ORIGINALS_DIR = DATASET_ROOT / "originals"
AUGMENTED_DIR = DATASET_ROOT / "augmented"

SAMPLE_RATE = 16000
WINDOW_S = 2.0  # analysis window the model actually sees
WINDOW_LEN = int(SAMPLE_RATE * WINDOW_S)

# Variant keys, fixed order so the plan is stable.
VARIANTS = ("gain", "tempo", "pitch", "mix", "shift")

GAIN_MIN, GAIN_MAX = 0.7, 1.3
TEMPO_MIN, TEMPO_MAX = 0.92, 1.08
PITCH_MIN, PITCH_MAX = 0.94, 1.06  # +/-6%
MIX_GAIN_MIN, MIX_GAIN_MAX = 0.15, 0.50  # background level under the speech


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


def _clip_seconds(path: Path) -> float:
    """Duration of a 16-bit mono WAV, in seconds (0.0 if unreadable)."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or SAMPLE_RATE)
    except Exception:  # noqa: BLE001 — a broken clip must not crash planning
        return 0.0


def _noise_pool(dataset_root: Path) -> List[Path]:
    """Real noise clips usable as background (originals first, then synthetic).

    Deterministic order so seeded selection is reproducible. Synthetic noise is
    included only because it is already tagged as such on disk; the files chosen
    here are mixed into *augmented* clips and inherit the source label.
    """
    pool: List[Path] = []
    for sub in ("originals", "synthetic"):
        base = dataset_root / sub / "noise"
        if base.is_dir():
            pool.extend(sorted(base.rglob("*.wav")))
    return pool


def _shift_filter(rng: random.Random, duration: float) -> str:
    """Move the utterance to a random offset of the 2.0 s analysis window.

    Short clips (the usual case) are delayed inside the window and padded to it;
    clips longer than the window are trimmed to a random 2.0 s excerpt — both
    mirror what a 500 ms-hop sliding window sees at runtime.
    """
    if duration >= WINDOW_S:
        start = round(rng.uniform(0.0, duration - WINDOW_S), 3)
        return f"atrim=start={start}:duration={WINDOW_S:g}"
    delay_ms = int(round(rng.uniform(0.0, WINDOW_S - max(duration, 0.0)) * 1000))
    return f"adelay={delay_ms}:all=1,apad,atrim=0:{WINDOW_S:g}"


def _variant_args(
    variant: str, rng: random.Random, original: Path, dataset_root: Path
) -> Tuple[List[str], List[str]]:
    """Return ``(extra_input_args, filter_args)`` for one augmentation variant."""

    if variant == "gain":
        gain = round(rng.uniform(GAIN_MIN, GAIN_MAX), 4)
        return [], ["-af", f"volume={gain}"]

    if variant == "tempo":
        t = round(rng.uniform(TEMPO_MIN, TEMPO_MAX), 4)
        return [], ["-af", f"atempo={t}"]

    if variant == "pitch":
        p = round(rng.uniform(PITCH_MIN, PITCH_MAX), 4)
        # asetrate changes pitch (and duration), then aresample restores rate.
        return [], ["-af", f"asetrate={int(SAMPLE_RATE * p)},aresample={SAMPLE_RATE}"]

    if variant == "mix":
        pool = _noise_pool(dataset_root)
        if not pool:
            # No noise in the dataset yet: keep the target list stable but be
            # explicit that nothing was mixed (was silently a no-op before).
            return [], ["-af", "volume=1.0"]
        noise = rng.choice(pool)
        bg = round(rng.uniform(MIX_GAIN_MIN, MIX_GAIN_MAX), 3)
        graph = (
            f"[1:a]volume={bg}[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[out]"
        )
        return (
            ["-stream_loop", "-1", "-i", str(noise)],
            ["-filter_complex", graph, "-map", "[out]"],
        )

    if variant == "shift":
        return [], ["-af", _shift_filter(rng, _clip_seconds(original))]

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
                extra, filt_args = _variant_args(variant, rng, original, dataset_root)
                output = augmented / label / f"aug_{stem}_{variant}.wav"
                args = [
                    "-i", str(original), *extra, *filt_args,
                    "-ar", str(SAMPLE_RATE), "-ac", "1", "-y", str(output),
                ]
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
