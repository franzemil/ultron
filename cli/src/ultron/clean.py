"""Repository cleanup — ``ultron clean``.

Removes what ``uv`` / ``pip`` / ``pytest`` / the Arduino build produce, so the checkout can be
reclaimed without touching source, ``uv.lock``, ``.env``, ``dataset/`` or exported model files
(``*.onnx`` / ``*.tflite``).

Design rules kept from the shell version:

* an explicit target list — nothing is removed by a broad wildcard;
* ``--dry-run`` prints the list and sizes, removes nothing;
* confirmation is required unless ``--yes`` is passed, and a non-TTY without ``--yes`` refuses
  rather than guessing;
* ``--uv-cache`` is opt-in because it clears the **shared global** uv cache (other projects).

`scripts/` no longer exists: every command that used to be a shell script is a typer command,
so nothing has to be discovered in a directory first.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Directories created by uv / pip / pytest / builds. Explicit so nothing is removed by a
# broad wildcard.
BASE_DIRS = (
    ".venv",
    "build",
    "dist",
    "dsp/build",
    "cli/build",
    "model/build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".coverage",
    "htmlcov",
)
BASE_FILES = ("dsp/mfcc_host_test.c", "coverage.xml")
PRUNE = {".git", ".venv", "dataset"}


@dataclass(frozen=True)
class Target:
    path: str
    is_dir: bool
    size: str

    def describe(self) -> str:
        kind = "dir " if self.is_dir else "file"
        if self.is_dir:
            return f"  dir   {self.path:<45} {self.size}"
        return f"  file  {self.path}"


def _size(path: Path) -> str:
    if not path.is_dir():
        return ""
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    for unit in ("B", "K", "M", "G"):
        if total < 1024 or unit == "G":
            return f"{total:.0f}{unit}" if unit == "B" else f"{total / 1:.0f}{unit}"
        total /= 1024
    return f"{total:.0f}G"


def discover(root: Path = REPO_ROOT) -> list[Target]:
    """Every cleanup target that currently exists, in a stable order."""
    found: list[Target] = []
    seen: set[str] = set()

    def add(rel: str) -> None:
        if rel in seen:
            return
        path = root / rel
        if not (path.exists() or path.is_symlink()):
            return
        seen.add(rel)
        found.append(Target(rel, path.is_dir(), _size(path)))

    for rel in BASE_DIRS:
        add(rel)
    for rel in BASE_FILES:
        add(rel)

    # Recursive byproducts, pruned so .git / .venv / dataset are never walked.
    for path in sorted(root.rglob("__pycache__")):
        if not PRUNE & set(path.relative_to(root).parts):
            add(str(path.relative_to(root)))
    for pattern in ("*.egg-info",):
        for path in sorted(root.rglob(pattern)):
            if not PRUNE & set(path.relative_to(root).parts):
                add(str(path.relative_to(root)))
    for pattern in ("*.pyc", "*.pyo"):
        for path in sorted(root.rglob(pattern)):
            if not PRUNE & set(path.relative_to(root).parts):
                add(str(path.relative_to(root)))
    return found


def clean(
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
    uv_cache: bool = False,
    interactive: bool | None = None,
) -> int:
    """Remove the targets. Returns 0 on success, 1 when refused/aborted."""
    import sys

    targets = discover()
    if not targets and not uv_cache:
        print("clean: nothing to remove — already clean.")
        return 0

    print(f"clean: targets under {REPO_ROOT}")
    for target in targets:
        print(target.describe())
    if uv_cache:
        print("  extra uv cache clean (shared global cache — affects other projects)")

    if dry_run:
        print("clean: dry run — nothing removed.")
        return 0

    if not assume_yes:
        can_prompt = interactive if interactive is not None else sys.stdin.isatty()
        if not can_prompt:
            print("clean: no TTY for confirmation; re-run with --yes.", file=sys.stderr)
            return 1
        reply = input("Remove the targets above? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            print("clean: aborted.")
            return 1

    for target in targets:
        path = REPO_ROOT / target.path
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            print(f"clean: could not remove {target.path}: {exc}", file=sys.stderr)

    if uv_cache:
        import subprocess

        if shutil.which("uv"):
            subprocess.run(["uv", "cache", "clean"], check=False)
        else:
            print("clean: uv not on PATH — skipped cache clean", file=sys.stderr)

    print("clean: done. Recreate with 'uv sync' and 'make -C dsp'.")
    return 0
