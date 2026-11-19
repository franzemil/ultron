"""Synthetic noise corpus assembly (T5.1).

Builds a background-mixing noise corpus from ffmpeg `anoisesrc` sources:

* ``room``   — pink/brown ambience at low amplitude
* ``car``    — low rumble with tremolo bursts (engine idle / pass-by)

Every clip is 16 kHz / 16-bit / mono WAV. Seeded: same seed => identical corpus.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

SAMPLE_RATE = 16000

# (source, ffmpeg lavfi filter graph, amplitude) recipes
SOURCE_RECIPES: dict[str, str] = {
    "room": "anoisesrc=color=pink:duration={seconds}:amplitude=0.08:seed={seed}",
    "car":  "anoisesrc=color=brown:duration={seconds}:amplitude=0.15:seed={seed},tremolo=f=7:d=0.35",
}

DEFAULT_SOURCES = ("room", "car")
DEFAULT_COUNT_PER_SOURCE = 8
DEFAULT_SECONDS = 4


class NoiseError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Fall back to the ffmpeg binary vendored by imageio-ffmpeg (installable
    # via uv on Windows, where ffmpeg is not on PATH by default).
    try:
        import imageio_ffmpeg  # type: ignore

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except ImportError:
        pass
    raise NoiseError(
        "ffmpeg is required for noise assembly (ffmpeg not found on PATH). "
        "Install it (e.g. `winget install ffmpeg`, `choco install ffmpeg`, "
        "or `conda install -c conda-forge ffmpeg`) or run `uv sync` to pull "
        "the vendored binary via imageio-ffmpeg."
    )


def build_noise_corpus(
    output: Path | str,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    seed: int = 42,
    count_per_source: int = DEFAULT_COUNT_PER_SOURCE,
    seconds: int = DEFAULT_SECONDS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Generate `count_per_source` noise clips per source under output/<source>/.

    Returns {source: count_written}. Dry-run prints the plan, writes nothing.
    """
    output = Path(output)
    rng = random.Random(seed)
    ffmpeg = require_ffmpeg()
    counts: dict[str, int] = {}

    for source in sources:
        if source not in SOURCE_RECIPES:
            raise NoiseError(f"unknown noise source: {source} (available: {sorted(SOURCE_RECIPES)})")
        source_dir = output / source
        counts[source] = 0
        for i in range(1, count_per_source + 1):
            lavfi = SOURCE_RECIPES[source].format(seconds=seconds, seed=rng.randrange(2**31))
            out = source_dir / f"syn_noise_{source}_{i:02d}.wav"
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", lavfi,
                "-ar", str(SAMPLE_RATE),
                "-ac", "1",
                "-sample_fmt", "s16",
                "-acodec", "pcm_s16le",
                str(out),
            ]
            if dry_run:
                print("  " + " ".join(cmd))
                counts[source] += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise NoiseError(
                    f"ffmpeg failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()[:500]}"
                )
            counts[source] += 1
    return counts
