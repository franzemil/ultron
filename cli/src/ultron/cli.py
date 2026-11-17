"""Ultron command-line interface.

Spanish voice-actuation car manager: ``hey ultron`` wake word, then a command
phrase drives 9 relay channels. This skeleton wires up Typer, logging and the
configuration loader; real commands land in later tickets.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
console = Console()

from .config import config
from .logging import logger, setup_logging
from ultron.dataset import ingest, validate, dc_fix
from ultron.record import record_command
from ultron.record_long import record_long_command
from ultron.split import split_command

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
app.command(name="record")(record_command)
app.command(name="record-long")(record_long_command)
app.command(name="split")(split_command)


@app.command(name="record-split")
def record_split_command(
    seconds: float = typer.Option(20.0, "--seconds", "-s", help="Seconds to record (default 20)."),
    label: str = typer.Argument(..., help="Label slug (registry id, e.g. lb, wake, noise)."),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    hf_token: Optional[str] = typer.Option(
        None, "--hf-token", "-t", help="Hugging Face API token (or set PYANNOTE_HF_TOKEN)."
    ),
    out_dir: str = typer.Option(
        "dataset", "--out-dir", "-o", help="Dataset output directory (default dataset/)."
    ),
) -> None:
    """Record a continuous take then split it into per-utterance WAVs - one command.

    Records --seconds of audio as a take, runs pyannote.audio VAD on it, and
    writes each speech segment as {label}_local_<nn>.wav under
    dataset/originals/<label>/. Single label per take; no STT needed.
    """
    from pathlib import Path

    from ultron.dataset import REGISTRY_IDS

    if label not in set(REGISTRY_IDS):
        console.print(f"[red]Unknown label '{label}'.[/red]")
        console.print(f"Valid codes: {', '.join(sorted(REGISTRY_IDS))}")
        raise typer.Exit(code=2)

    takes_dir = Path(out_dir) / "takes"
    take_path = record_long_command(
        seconds=seconds, label=label, device=device, out_dir=str(takes_dir)
    )
    take_path = str(take_path)

    console.print()
    split_command(
        take=str(take_path),
        label=label,
        hf_token=hf_token,
        out_dir=out_dir,
    )


__all__ = ["main"]


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


@app.command(name="config")
def config_info() -> None:
    """Show whether the Edge Impulse key is configured (value hidden)."""
    typer.echo(f"EI API key configured: {config.configured}")


@app.command(name="version")
def version() -> None:
    """Show the ultron version."""
    from ultron import __version__

    typer.echo(f"ultron {__version__}")


def main() -> None:
    setup_logging()
    logger.debug("ultron CLI starting")
    app()


if __name__ == "__main__":
    main()
