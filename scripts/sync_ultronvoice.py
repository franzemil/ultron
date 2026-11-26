#!/usr/bin/env python3
"""Sync the shared C sources into the UltronVoice Arduino library.

The library (`devices/esp32/libraries/UltronVoice/`) must be self-contained: an Arduino
library can only build files inside its own `src/`, so the shared DSP library cannot be
`#include`d from `dsp/`. It is therefore **vendored as a byte-identical copy**, and this
script is the only thing allowed to write that copy:

    dsp/mfcc.c  ──►  devices/esp32/libraries/UltronVoice/src/mfcc.c
    dsp/mfcc.h  ──►  devices/esp32/libraries/UltronVoice/src/mfcc.h

`--check` re-verifies the copies and exits 1 on any drift, which is what
`tests/test_esp32_config.py` runs — so the firmware cannot run a stale MFCC while the host
pipeline (and the Pi runtime) moved on. That keeps the "same bytes as the host" promise
(tests/test_dsp_parity.py) true across the library boundary.

Usage:
    uv run python scripts/sync_ultronvoice.py           # copy (idempotent)
    uv run python scripts/sync_ultronvoice.py --check   # fail on drift
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_SRC = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src"
SOURCES = ("mfcc.c", "mfcc.h")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, do not copy")
    args = parser.parse_args(argv)

    problems: list[str] = []
    for name in SOURCES:
        master = REPO_ROOT / "dsp" / name
        copy = LIB_SRC / name
        if not master.is_file():
            problems.append(f"missing master {master}")
            continue
        if not args.check:
            LIB_SRC.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(master, copy)
            print(f"sync_ultronvoice: {master.relative_to(REPO_ROOT)} → {copy.relative_to(REPO_ROOT)}")
            continue
        if not copy.is_file():
            problems.append(f"missing copy {copy.relative_to(REPO_ROOT)} — run scripts/sync_ultronvoice.py")
        elif digest(copy) != digest(master):
            problems.append(
                f"{copy.relative_to(REPO_ROOT)} differs from dsp/{name} — run scripts/sync_ultronvoice.py"
            )

    if args.check:
        if problems:
            for p in problems:
                print(f"sync_ultronvoice: {p}", file=sys.stderr)
            return 1
        print("sync_ultronvoice: library copies are byte-identical to dsp/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
