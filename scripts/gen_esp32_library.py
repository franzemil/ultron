#!/usr/bin/env python3
"""Refresh the UltronVoice Arduino library from the current model artifacts.

**Thin wrapper.** The implementation lives in the CLI package
(``cli/src/ultron/esp32lib.py``) so there is exactly one code path shared by:

    uv run ultron esp32 lib --sync            # the CLI command (preferred)
    uv run python scripts/gen_esp32_library.py # this wrapper: same function, no typer
    uv run ultron export tflite               # refreshes automatically for --out build/

Why a wrapper at all: ``scripts/deploy_xiao.sh`` and the docs call a plain script, and CI
should not need the CLI's optional dependencies to verify artifacts.

It writes, in the library's ``src/``: ``mfcc.c``/``mfcc.h`` (byte-identical to ``dsp/``),
``ultron_config.h`` (thresholds + registry), ``model_data.h`` (the flatbuffer), and bumps
``library.properties`` with these rules:

* nothing changed   → no bump, no writes (idempotent)
* config or model   → patch + 1
* frozen class order → minor + 1

Usage:
    uv run python scripts/gen_esp32_library.py                    # refresh + bump if needed
    uv run python scripts/gen_esp32_library.py --placeholder      # placeholder model build
    uv run python scripts/gen_esp32_library.py --no-bump          # refresh, keep the version
    uv run python scripts/gen_esp32_library.py --check            # stale? exit 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The implementation is in the CLI package; add it to sys.path when running from a plain
# checkout (the console script already has it via the editable install).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli" / "src"))

from ultron import esp32lib  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--model", type=Path, default=None,
                        help="flatbuffer (default: build/placeholder/model_int8.tflite with "
                             "--placeholder, else build/model_int8.tflite)")
    parser.add_argument("--placeholder", action="store_true",
                        help="mark the embedded model as the deterministic placeholder")
    parser.add_argument("--labels", type=Path, default=None,
                        help="frozen class order (build/labels.json); default: derive")
    parser.add_argument("--no-bump", action="store_true", help="refresh without changing the version")
    parser.add_argument("--check", action="store_true", help="never write; exit 1 when stale")
    args = parser.parse_args(argv)

    try:
        if args.check:
            ok, problems, notes = esp32lib.check(
                model=args.model, placeholder=args.placeholder, labels=args.labels
            )
            for note in notes:
                print(f"gen_esp32_library: {note}", file=sys.stderr)
            if not ok:
                print(f"gen_esp32_library: library is STALE ({', '.join(problems)}) — run "
                      "`uv run python scripts/gen_esp32_library.py`", file=sys.stderr)
                return 1
            print(f"gen_esp32_library: library v{esp32lib.read_version()} matches model/ "
                  "(no drift)")
            return 0

        result = esp32lib.refresh(
            model=args.model,
            placeholder=args.placeholder,
            labels=args.labels,
            bump=not args.no_bump,
        )
    except esp32lib.Esp32LibError as exc:
        print(f"gen_esp32_library: {exc}", file=sys.stderr)
        return 2

    for line in result.summary_lines():
        print(f"gen_esp32_library: {line.strip()}")
    if result.bumped:
        print("  next         : ./scripts/deploy_xiao.sh   (compile + flash picks it up)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
