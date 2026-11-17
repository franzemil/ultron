"""Ultron command-line interface.

Spanish voice-actuation car manager: ``hey ultron`` wake word, then a command
phrase drives 9 relay channels. This skeleton wires up Typer, logging and the
configuration loader; real commands land in later tickets.
"""

from __future__ import annotations

import typer

from .config import config
from .logging import logger, setup_logging
from ultron.dataset import ingest, validate, dcfix

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


def main() -> None:
    setup_logging()
    logger.debug("ultron CLI starting")
    app()


if __name__ == "__main__":
    main()
