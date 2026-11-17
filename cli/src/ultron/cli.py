"""Ultron command-line interface.

Spanish voice-actuation car manager: ``hey ultron`` wake word, then a command
phrase drives 9 relay channels. Includes seeded ffmpeg augmentation (T5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import questionary
import typer
from rich.console import Console

from .augment import AUGMENTED_DIR, DATASET_ROOT, ORIGINALS_DIR, plan_augmentations, run_plan
from .config import config
from .logging import logger, setup_logging
from ultron.dataset import REGISTRY_IDS, ingest, validate, dc_fix, export_zip, import_zip
from ultron.record import record_command
from ultron.record_long import record_long_command
from ultron.split import _phrase_for, _registry, split_command
from . import synth, tts

console = Console()

app = typer.Typer(
    name="ultron",
    help="Ultron — Spanish voice-actuation car manager.",
    no_args_is_help=True,
)

dataset = typer.Typer(
    name="dataset",
    help="Dataset tooling: ingest, validate, dcfix.",
)
app.add_typer(dataset, name="dataset")

augment = typer.Typer(
    name="augment",
    help="Seeded ffmpeg augmentation (T5).",
)
app.add_typer(augment, name="augment")

synth_app = typer.Typer(
    name="synth",
    help="Synthetic data generation (tts, noise).",
)
app.add_typer(synth_app, name="synth")
app.command(name="record")(record_command)
app.command(name="record-long")(record_long_command)
app.command(name="split")(split_command)


@augment.command("plan")
def augment_plan(
    seed: int = typer.Option(42, "--seed", "-s", help="RNG seed (deterministic plan)."),
    dataset: Path = typer.Option(DATASET_ROOT, "--dataset", "-d", help="Dataset root."),
) -> None:
    """List augmentation targets before writing anything (dry-run by design)."""
    targets = plan_augmentations(seed=seed, dataset_root=dataset)
    if not targets:
        typer.secho(
            f"no originals found under {dataset / 'originals'} — nothing to plan.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)
    typer.echo(f"augment plan (seed={seed}): {len(targets)} target(s)")
    for t in targets:
        typer.echo(t.plan_line())


@augment.command("run")
def augment_run(
    seed: int = typer.Option(42, "--seed", "-s", help="RNG seed (deterministic plan)."),
    dataset: Path = typer.Option(DATASET_ROOT, "--dataset", "-d", help="Dataset root."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List targets without invoking ffmpeg.",
    ),
) -> None:
    """Execute the augmentation plan (writes augmented/<label>/aug_*.wav)."""
    targets = plan_augmentations(seed=seed, dataset_root=dataset)
    if not targets:
        typer.secho(
            f"no originals found under {dataset / 'originals'} — nothing to run.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)
    messages = run_plan(targets, dry_run=dry_run)
    for m in messages:
        typer.echo(m)
    if dry_run:
        typer.secho("dry-run complete: nothing was written.", fg=typer.colors.YELLOW)
    else:
        typer.secho(f"augment run complete: {len(targets)} target(s).", fg=typer.colors.GREEN)


def _pick_label(registry: dict) -> str:
    """Interactive rich prompt picker: choose a label to record.

    Shows each registry code with its Spanish phrase.
    """
    codes = sorted(REGISTRY_IDS)
    choices = []
    for code in codes:
        phrase = _phrase_for(code, registry)
        choices.append(
            questionary.Choice(title=f"{code:<10} {phrase}", value=code)
        )
    selected = questionary.select(
        "Select a label to record:",
        choices=choices,
    ).ask()
    if selected is None:
        console.print("Aborted.")
        raise typer.Exit(code=0)
    return selected


@app.command(name="record-split")
def record_split_command(
    seconds: float = typer.Option(20.0, "--seconds", "-s", help="Seconds to record (default 20)."),
    label: Optional[str] = typer.Argument(
        None, help="Label slug (registry id, e.g. lb, wake, noise). Prompted if omitted."
    ),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    out_dir: str = typer.Option(
        "dataset", "--out-dir", "-o", help="Dataset output directory (default dataset/)."
    ),
) -> None:
    """Record a continuous take then split it into per-utterance WAVs - one command.

    Records --seconds of audio as a take, runs Silero VAD on it, and
    writes each speech segment as {label}_local_<nn>.wav under
    dataset/originals/<label>/. Single label per take; no STT needed.
    If --label is omitted, an interactive prompt lets you pick from the
    registry codes (with Spanish phrases).
    """
    from pathlib import Path as _Path
    from ultron.dataset import REGISTRY_IDS as _REGISTRY_IDS
    registry = _registry()
    if label is None:
        label = _pick_label(registry)
    if label not in set(_REGISTRY_IDS):
        console.print(f"[red]Unknown label '{label}'.[/red]")
        console.print(f"Valid codes: {', '.join(sorted(_REGISTRY_IDS))}")
        raise typer.Exit(code=2)
    takes_dir = _Path(out_dir) / "takes"
    take_path = record_long_command(
        seconds=seconds, label=label, device=device, out_dir=str(takes_dir)
    )
    take_path = str(take_path)
    console.print()
    split_command(
        take=str(take_path),
        label=label,
        out_dir=out_dir,
    )


@dataset.command(name="ingest")
def dataset_ingest(
    source: str = typer.Argument(..., help="Source directory containing WAV files to ingest."),
    label: str = typer.Argument(..., help="Action label slug (e.g. lm, lb, gd, wake, noise)."),
    mic: str = typer.Option(
        "ext",
        "--mic",
        "-m",
        help="Mic source tag: xiao (XIAO PDM), pi (Pi USB mic), or ext (owner-supplied external).",
    ),
    dest: str = typer.Option(
        "dataset/originals",
        "--dest",
        "-d",
        help="Destination dataset directory (defaults to dataset/originals/<label>).",
    ),
) -> None:
    """Copy and label WAVs from ANY source (device or owner-supplied external recordings)."""
    ingest(source=source, label=label, mic=mic, dest=dest)


@dataset.command(name="validate")
def dataset_validate(
    path: str = typer.Argument(
        "dataset", help="Dataset directory to validate (defaults to dataset/)."
    )
) -> None:
    """Check every clip is 16 kHz / 16-bit / mono; exit non-zero listing each failing file."""
    raise typer.Exit(code=validate(path=path))


@dataset.command(name="dcfix")
def dataset_dcfix(
    path: str = typer.Argument(
        "dataset", help="Dataset directory of WAVs to DC-offset-correct in place."
    )
) -> None:
    """Remove DC offset from every WAV under <path> (recenter waveform around zero)."""
    dc_fix(path=path)


@dataset.command(name="export")
def dataset_export(
    path: str = typer.Argument("dataset", help="Dataset directory to zip (default dataset/)."),
    out: str = typer.Option(
        "dataset_export.zip", "--out", "-o", help="Output zip path."
    ),
) -> None:
    """Zip the dataset so recordings survive beyond the worktree."""
    export_zip(path=path, out=out)


@dataset.command(name="import")
def dataset_import(
    zip_path: str = typer.Argument(..., help="Zip file produced by 'ultron dataset export'."),
    dest: str = typer.Option("dataset", "--dest", "-d", help="Destination dataset directory."),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing files."
    ),
) -> None:
    """Import a dataset zip into <dest> (recordings back into the worktree)."""
    import_zip(zip_path=zip_path, dest=dest, force=force)


@app.command(name="config")
def config_info() -> None:
    """Show whether the Edge Impulse key is configured (value hidden)."""
    typer.echo(f"EI API key configured: {config.configured}")


@app.command(name="version")
def version() -> None:
    """Show the ultron version."""
    from ultron import __version__

    typer.echo(f"ultron {__version__}")


@synth_app.command(name="tts")
def synth_tts(
    output: str = typer.Option(
        "dataset/synthetic", "--output", "-o", help="Output directory for synthetic TTS clips"
    ),
    seed: int = typer.Option(42, "--seed", help="RNG seed (same seed => identical synthetic set)"),
    voices: str = typer.Option(
        "", "--voices", help="Comma-separated voice names (default: engine-dependent Spanish voices)"
    ),
    speeds: str = typer.Option(
        "0.9,1.0,1.1", "--speeds", help="Comma-separated speaking speeds"
    ),
    engine: str = typer.Option(
        "auto", "--engine", help="TTS engine: say (macOS), mock (deterministic offline), auto"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List the render plan without writing any file"
    ),
) -> None:
    """Render every registered phrase in several Spanish TTS voices x speeds (seeded)."""
    voice_list = tuple(v.strip() for v in voices.split(",") if v.strip()) if voices else ()
    speed_list = tuple(s.strip() for s in speeds.split(",") if s.strip())
    if not voice_list:
        voice_list = tuple(tts.say_voices()) or tts.DEFAULT_MOCK_VOICES
    if not speed_list:
        speed_list = tts.DEFAULT_MOCK_SPEEDS

    rendered = synth.render_tts(
        voices=voice_list,
        speeds=speed_list,
        output=Path(output),
        seed=seed,
        engine=engine,
        dry_run=dry_run,
    )
    if dry_run:
        typer.echo("DRY-RUN complete — nothing written.")
        return
    total = sum(rendered.values())
    typer.echo(
        f"TTS render complete: {total} clips across {len(voice_list)} voices x {len(speed_list)} speeds"
    )
    for label, count in sorted(rendered.items()):
        typer.echo(f"  {label}: {count}")


@synth_app.command(name="noise")
def synth_noise(
    output: str = typer.Option(
        "dataset/synthetic/noise", "--output", "-o", help="Output directory for the noise corpus"
    ),
    seed: int = typer.Option(42, "--seed", help="RNG seed (same seed => identical corpus)"),
    sources: str = typer.Option(
        "room,car", "--sources", help="Comma-separated noise sources (room, car)"
    ),
    count: int = typer.Option(8, "--count", help="Clips per source"),
    seconds: int = typer.Option(4, "--seconds", help="Seconds per noise clip"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List the assembly plan without writing any file"
    ),
) -> None:
    """Assemble a noise corpus (room ambience, car/engine sounds) for background mixing."""
    source_list = tuple(s.strip() for s in sources.split(",") if s.strip())
    counts = synth.render_noise(
        output=Path(output),
        sources=source_list,
        seed=seed,
        count_per_source=count,
        seconds=seconds,
        dry_run=dry_run,
    )
    if dry_run:
        typer.echo("DRY-RUN complete — nothing written.")
        return
    total = sum(counts.values())
    typer.echo(f"Noise corpus complete: {total} clips across {len(source_list)} sources")
    for source, count in sorted(counts.items()):
        typer.echo(f"  {source}: {count}")


def main() -> None:
    setup_logging()
    logger.debug("ultron CLI starting")
    app()


if __name__ == "__main__":
    main()
