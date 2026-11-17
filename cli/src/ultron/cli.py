"""Ultron command-line interface.

Spanish voice-actuation car manager: ``hey ultron`` wake word, then a command
phrase drives 9 relay channels. This skeleton wires up Typer, logging and the
configuration loader; real commands land in later tickets.
"""

from __future__ import annotations

import typer

from .config import config
from .logging import logger, setup_logging

app = typer.Typer(
    name="ultron",
    help="Ultron — Spanish voice-actuation car manager.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Show the ultron version."""
    from ultron import __version__

    typer.echo(f"ultron {__version__}")


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
