"""Pytest scaffold for the ultron CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from ultron.cli import app


def test_help_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ultron" in result.output


def test_version_command() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "ultron 0.1.0" in result.output
