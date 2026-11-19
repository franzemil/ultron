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
    labels: tuple[str, ...] | None = None,
    samples: int | None = None,
    seed: int = DEFAULT_SEED,
) -> list[TTSTarget]:
    """Plan the synthetic TTS renders.

    Default (``samples is None``): the full cartesian plan, phrase x voice x
    speed. With ``samples=N``: N *random draws* per label — phrase variant,
    voice and a speed jittered inside the given range — so 20 generated clips
    are 20 different ways of saying the command rather than a grid.

    Every name keeps the ``syn_`` prefix (``dataset.is_synthetic``) and lives
    under ``output/<label>/`` (``dataset/synthetic`` by default), which is what
    keeps generated audio out of the original dataset and out of the hold-out.
    Names are deterministic functions of (label, voice, speed, sequence) and the
    RNG is seeded, so same seed => identical plan.
    """
    phrases = dataset.registry_phrases(registry_path)
    wanted = set(labels) if labels else None
    if wanted:
        unknown = sorted(wanted - {label for label, _ in phrases})
        if unknown:
            raise ValueError(
                f"unknown label(s): {', '.join(unknown)}; "
                f"valid: {', '.join(sorted({label for label, _ in phrases}))}"
            )
        phrases = [(label, phrase) for label, phrase in phrases if label in wanted]

    targets: list[TTSTarget] = []
    seq: dict[tuple[str, str], int] = {}

    def _emit(label: str, phrase: str, voice: str, speed: str) -> None:
        key = (label, voice)
        seq[key] = seq.get(key, 0) + 1
        out = output / label / f"syn_{label}_{voice}_{speed}_{seq[key]:02d}.wav"
        targets.append(TTSTarget(label, phrase, voice, speed, out))

    if samples is None:
        for label, phrase in phrases:
            for voice in voices:
                for speed in speeds:
                    _emit(label, phrase, voice, speed)
        return targets

    if samples <= 0:
        raise ValueError(f"--samples must be positive, got {samples}")
    if not voices or not speeds:
        raise ValueError("random sampling needs at least one voice and one speed")

    numeric = sorted({float(s) for s in speeds})
    low, high = numeric[0], numeric[-1]
    rng = random.Random(seed)
    by_label: dict[str, list[str]] = {}
    order: list[str] = []
    for label, phrase in phrases:
        if label not in by_label:
            by_label[label] = []
            order.append(label)
        by_label[label].append(phrase)

    for label in order:  # sorted by registry order, independent of dict order
        variants = by_label[label]
        for _ in range(samples):
            phrase = rng.choice(variants)
            voice = rng.choice(voices)
            speed = f"{rng.uniform(low, high):.2f}"
            _emit(label, phrase, voice, speed)
    return targets


def render_tts(
    voices: tuple[str, ...],
    speeds: tuple[str, ...],
    output: Path | str,
    seed: int = DEFAULT_SEED,
    engine: str = "auto",
    dry_run: bool = False,
    registry_path: Path | str = "model/registry.json",
    labels: tuple[str, ...] | None = None,
    samples: int | None = None,
) -> dict[str, int]:
    """Render the TTS target set.

    Seed fixes render order (seeded shuffle) and, for the mock engine, the
    per-clip audio parameters — same seed => identical synthetic set.
    """
    output = Path(output)
    provider = tts.provider_for(engine, seed=seed)
    targets = tts_targets(
        voices, speeds, output, registry_path=registry_path, labels=labels, samples=samples, seed=seed
    )

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
