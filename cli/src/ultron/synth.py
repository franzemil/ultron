"""Synthetic data generation: `ultron synth tts` and `ultron synth noise` (T5.1).

Policy (lampara-aliz lesson, hard requirement):

* Synthetic clips are tagged `synthetic` (dataset/synthetic/, `syn_*` names).
* They NEVER enter the fresh hold-out and never count toward NFR-005
  original minimums.
* Training uses them only via `--with-synthetic` (default off — the owner
  decides per run); every use is a changelog event.
* Same seed => identical synthetic set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from . import dataset, noise, tts

DEFAULT_TTS_SPEEDS = ("0.9", "1.0", "1.1")
DEFAULT_SEED = 42


@dataclass(frozen=True)
class TTSTarget:
    label: str
    phrase: str
    voice: str
    speed: str
    out: Path


def tts_targets(
    voices: tuple[str, ...],
    speeds: tuple[str, ...],
    output: Path,
    registry_path: Path | str = "model/registry.json",
) -> list[TTSTarget]:
    """Plan every registered phrase x voice x speed.

    Target names are deterministic functions of (label, voice, speed, sequence)
    so the plan does not depend on render order or seed.
    """
    phrases = dataset.registry_phrases(registry_path)
    targets: list[TTSTarget] = []
    seq: dict[tuple[str, str], int] = {}
    for label, phrase in phrases:
        for voice in voices:
            key = (label, voice)
            seq[key] = seq.get(key, 0) + 1
            for speed in speeds:
                out = output / label / f"syn_{label}_{voice}_{speed}_{seq[key]:02d}.wav"
                targets.append(TTSTarget(label, phrase, voice, speed, out))
    return targets


def render_tts(
    voices: tuple[str, ...],
    speeds: tuple[str, ...],
    output: Path | str,
    seed: int = DEFAULT_SEED,
    engine: str = "auto",
    dry_run: bool = False,
    registry_path: Path | str = "model/registry.json",
) -> dict[str, int]:
    """Render the TTS target set.

    Seed fixes render order (seeded shuffle) and, for the mock engine, the
    per-clip audio parameters — same seed => identical synthetic set.
    """
    output = Path(output)
    provider = tts.provider_for(engine, seed=seed)
    targets = tts_targets(voices, speeds, output, registry_path=registry_path)

    rendered: dict[str, int] = {}
    if dry_run:
        print(f"DRY-RUN: would render {len(targets)} TTS targets into {output}:")
        for t in targets:
            print(f"  {t.out}")
        return rendered

    # Seeded render order — deterministic per seed.
    ordered = list(targets)
    random.Random(seed).shuffle(ordered)

    for t in ordered:
        t.out.parent.mkdir(parents=True, exist_ok=True)
        provider.render(t.phrase, t.voice, t.speed, t.out, dry_run=False)
        rendered[t.label] = rendered.get(t.label, 0) + 1
    return rendered


def render_noise(
    output: Path | str,
    sources: tuple[str, ...] = noise.DEFAULT_SOURCES,
    seed: int = DEFAULT_SEED,
    count_per_source: int = noise.DEFAULT_COUNT_PER_SOURCE,
    seconds: int = noise.DEFAULT_SECONDS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Assemble the noise corpus (room ambience + car/engine sounds)."""
    return noise.build_noise_corpus(
        output=output,
        sources=sources,
        seed=seed,
        count_per_source=count_per_source,
        seconds=seconds,
        dry_run=dry_run,
    )
