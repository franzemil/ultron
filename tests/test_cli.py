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


# ── `ultron clean` (scripts/ is gone: the CLI is the only surface) ────────────


def test_clean_dry_run_lists_and_removes_nothing(tmp_path, monkeypatch):
    from ultron import clean as clean_mod

    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "x.bin").write_bytes(b"x" * 2048)
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "a.pyc").write_bytes(b"pyc")
    monkeypatch.setattr(clean_mod, "REPO_ROOT", tmp_path)

    rc = clean_mod.clean(dry_run=True)
    assert rc == 0
    assert (tmp_path / "build" / "x.bin").is_file(), "dry run must not delete"
    names = [t.path for t in clean_mod.discover(tmp_path)]
    assert any("build" in n for n in names)
    assert any("__pycache__" in n for n in names)


def test_clean_removes_targets_with_yes(tmp_path, monkeypatch):
    from ultron import clean as clean_mod

    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "x.bin").write_bytes(b"x")
    (tmp_path / "keep.txt").write_text("source stays")
    monkeypatch.setattr(clean_mod, "REPO_ROOT", tmp_path)

    assert clean_mod.clean(assume_yes=True) == 0
    assert not (tmp_path / "build").exists()
    assert (tmp_path / "keep.txt").is_file()


def test_clean_refuses_without_tty_and_confirmation(tmp_path, monkeypatch):
    from ultron import clean as clean_mod

    (tmp_path / "build").mkdir()
    monkeypatch.setattr(clean_mod, "REPO_ROOT", tmp_path)
    assert clean_mod.clean(interactive=False) == 1
    assert (tmp_path / "build").is_dir(), "must not delete without consent"


def test_clean_never_walks_dataset_or_git(tmp_path, monkeypatch):
    from ultron import clean as clean_mod

    for rel in (".git", "dataset", "build"):
        (tmp_path / rel).mkdir()
    (tmp_path / "dataset" / "__pycache__").mkdir()
    monkeypatch.setattr(clean_mod, "REPO_ROOT", tmp_path)
    names = [t.path for t in clean_mod.discover(tmp_path)]
    assert not any("dataset" in n for n in names)
    assert not any(".git" in n for n in names)
