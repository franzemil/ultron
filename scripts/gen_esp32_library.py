#!/usr/bin/env python3
"""Refresh the UltronVoice Arduino library from the current model artifacts, with a version bump.

One command that keeps `devices/esp32/libraries/UltronVoice/` honest after an export or a
threshold change:

    dsp/mfcc.c/.h ──► src/mfcc.c/.h                     (byte-identical, drift-guarded)
    model_int8.tflite ──► src/model_data.h              (embedded flatbuffer)
    thresholds.json + registry.json ──► src/ultron_config.h (gates + class order)
    library.properties  ──► version bumped when any of the above changed

Why a version: the embedded model and the generated config are what a flashed board *is*, so
"which firmware is this?" must be answerable from the console. The generated header carries
`ULTRON_LIBRARY_VERSION` and the boot banner prints it next to the model sha256:

    DBG boot fw=ultron_voice lib=0.1.2 task=T12 console_only=1 …

Bump rules (deterministic, idempotent):

* nothing changed              → no bump, no writes (safe to run in a loop / in CI)
* config or model bytes changed → patch + 1
* frozen class order changed    → minor + 1 and patch reset to 0 (a consumer that hard-coded an
  index must notice; the model's output mapping is part of the library's contract)

`--check` never writes: it fails when the committed library is stale (that is what
`scripts/deploy_xiao.sh` and the test suite run, so a board can never be flashed with artifacts
that disagree with `model/`).

Usage:
    uv run python scripts/gen_esp32_library.py                    # refresh + bump if needed
    uv run python scripts/gen_esp32_library.py --placeholder      # placeholder model build
    uv run python scripts/gen_esp32_library.py --no-bump          # refresh, keep the version
    uv run python scripts/gen_esp32_library.py --check            # stale? exit 1
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_esp32_config  # noqa: E402
import gen_esp32_model  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "devices/esp32/libraries/UltronVoice"
LIB_PROPERTIES = LIB_DIR / "library.properties"
LIB_SRC = LIB_DIR / "src"
VERSION_RE = re.compile(r"^version=(\d+)\.(\d+)\.(\d+)\s*$", re.MULTILINE)


class LibraryError(RuntimeError):
    """Loud failure — never write a half-updated library."""


def read_version() -> str:
    if not LIB_PROPERTIES.is_file():
        raise LibraryError(f"library.properties not found: {LIB_PROPERTIES}")
    match = VERSION_RE.search(LIB_PROPERTIES.read_text(encoding="utf-8"))
    if not match:
        raise LibraryError(f"{LIB_PROPERTIES}: no 'version=MAJOR.MINOR.PATCH' line")
    return ".".join(match.group(i) for i in (1, 2, 3))


def bump_version(version: str, level: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(version: str) -> None:
    text = LIB_PROPERTIES.read_text(encoding="utf-8")
    new, count = VERSION_RE.subn(f"version={version}\n", text)
    if count != 1:
        raise LibraryError(f"{LIB_PROPERTIES}: expected exactly one version line, found {count}")
    LIB_PROPERTIES.write_text(new, encoding="utf-8")


def render_config(labels_path: Path | None) -> str:
    """Render the generated config header without touching the disk."""
    try:
        return gen_esp32_config.generate(
            out_path=LIB_SRC / "ultron_config.h",
            thresholds_path=gen_esp32_config.DEFAULT_THRESHOLDS,
            registry_path=gen_esp32_config.DEFAULT_REGISTRY,
            labels_path=labels_path if labels_path is not None else gen_esp32_config.DEFAULT_LABELS,
            library_version=read_version(),
        )
    except gen_esp32_config.ConfigError as exc:
        raise LibraryError(str(exc)) from exc


def render_all(model_path: Path, placeholder: bool, labels_path: Path | None) -> dict[str, str]:
    """Render every generated file without touching the disk (needs the flatbuffer)."""
    version = read_version()
    try:
        config_text = gen_esp32_config.generate(
            out_path=LIB_SRC / "ultron_config.h",
            thresholds_path=gen_esp32_config.DEFAULT_THRESHOLDS,
            registry_path=gen_esp32_config.DEFAULT_REGISTRY,
            labels_path=labels_path if labels_path is not None else gen_esp32_config.DEFAULT_LABELS,
            library_version=version,
        )
        model_text = gen_esp32_model.generate(
            model_path,
            LIB_SRC / "model_data.h",
            LIB_SRC / "ultron_config.h",
            placeholder=placeholder,
            provenance=None,
            check=False,
        )
    except (gen_esp32_config.ConfigError, gen_esp32_model.ModelDataError) as exc:
        raise LibraryError(str(exc)) from exc
    return {"config": config_text, "model": model_text}


def class_order_of(config_text: str) -> list[str]:
    match = re.search(r"ULTRON_CLASS_NAMES\[[^\]]*\]\s*=\s*\{([^}]*)\}", config_text)
    names = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)) if match else []
    if not names:
        raise LibraryError("rendered config has no ULTRON_CLASS_NAMES")
    return names


def current_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def embedded_class_count(model_header: str) -> int | None:
    match = re.search(r"^#define ULTRON_MODEL_CLASS_COUNT (\d+)$", model_header, re.MULTILINE)
    return int(match.group(1)) if match else None


def check(args, model_path: Path) -> int:
    """Never write. Verifies the committed library against model/ as far as possible."""
    problems: list[str] = []
    if current_text(LIB_SRC / "ultron_config.h") != render_config(args.labels):
        problems.append("ultron_config.h")
    for name in ("mfcc.c", "mfcc.h"):
        master, copy = REPO_ROOT / "dsp" / name, LIB_SRC / name
        if not copy.is_file() or copy.read_bytes() != master.read_bytes():
            problems.append(name)

    # The flatbuffer is gitignored: when it is absent (fresh clone) the model header can
    # only be checked for internal consistency with the config, which is still the
    # difference that breaks a flash (class count mismatch).
    if model_path.is_file():
        rendered = render_all(model_path, args.placeholder, args.labels)["model"]
        if current_text(LIB_SRC / "model_data.h") != rendered:
            problems.append("model_data.h")
    else:
        header = current_text(LIB_SRC / "model_data.h")
        n_embedded = embedded_class_count(header)
        n_config = len(class_order_of(render_config(args.labels)))
        if n_embedded is None:
            problems.append("model_data.h (missing ULTRON_MODEL_CLASS_COUNT)")
        elif n_embedded != n_config:
            problems.append(
                f"model_data.h ({n_embedded} classes) vs ultron_config.h ({n_config})")
        else:
            print(f"gen_esp32_library: {model_path.name} absent — verified the model header's "
                  f"class count against the config instead of its bytes", file=sys.stderr)

    if problems:
        print(f"gen_esp32_library: library is STALE ({', '.join(problems)}) — run "
              "`uv run python scripts/gen_esp32_library.py`", file=sys.stderr)
        return 1
    print(f"gen_esp32_library: library v{read_version()} matches model/ (no drift)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, default=None,
                        help="flatbuffer (default: build/placeholder/model_int8.tflite with "
                             "--placeholder, else build/model_int8.tflite)")
    parser.add_argument("--placeholder", action="store_true",
                        help="mark the embedded model as the deterministic placeholder")
    parser.add_argument("--labels", type=Path, default=None,
                        help="frozen class order (build/labels.json); default: derive")
    parser.add_argument("--no-bump", action="store_true", help="refresh without changing the version")
    parser.add_argument("--check", action="store_true",
                        help="never write; exit 1 when the committed library is stale")
    args = parser.parse_args(argv)

    model_path = args.model
    if model_path is None:
        model_path = (gen_esp32_model.DEFAULT_PLACEHOLDER_MODEL if args.placeholder
                      else gen_esp32_model.DEFAULT_MODEL)

    try:
        if args.check:
            return check(args, model_path)

        version = read_version()
        rendered = render_all(model_path, args.placeholder, args.labels)

        config_path = LIB_SRC / "ultron_config.h"
        model_out = LIB_SRC / "model_data.h"
        config_stale = current_text(config_path) != rendered["config"]
        model_stale = current_text(model_out) != rendered["model"]
        mfcc_stale = any(
            not (LIB_SRC / name).is_file()
            or (LIB_SRC / name).read_bytes() != (REPO_ROOT / "dsp" / name).read_bytes()
            for name in ("mfcc.c", "mfcc.h")
        )

        if not (config_stale or model_stale or mfcc_stale):
            print(f"gen_esp32_library: library v{version} already current "
                  f"(model sha256 {gen_esp32_model.hashlib.sha256(model_path.read_bytes()).hexdigest()[:12]}…)")
            return 0

        # class-order change is a contract change for consumers → minor bump
        level = "patch"
        if config_stale:
            old_order = class_order_of(current_text(config_path))
            new_order = class_order_of(rendered["config"])
            if old_order != new_order:
                level = "minor"

        if args.no_bump:
            new_version = version
        else:
            new_version = bump_version(version, level)
            write_version(new_version)
            rendered = render_all(model_path, args.placeholder, args.labels)  # config embeds it

        for name in ("mfcc.c", "mfcc.h"):
            shutil.copyfile(REPO_ROOT / "dsp" / name, LIB_SRC / name)
        (LIB_SRC / "ultron_config.h").write_text(rendered["config"], encoding="utf-8")
        (LIB_SRC / "model_data.h").write_text(rendered["model"], encoding="utf-8")
    except LibraryError as exc:
        print(f"gen_esp32_library: {exc}", file=sys.stderr)
        return 2

    model_sha = gen_esp32_model.hashlib.sha256(model_path.read_bytes()).hexdigest()
    print(f"gen_esp32_library: v{version} → v{new_version} ({level} bump)")
    print(f"  model        : {model_path.relative_to(REPO_ROOT) if model_path.is_relative_to(REPO_ROOT) else model_path}")
    print(f"  model sha256 : {model_sha}")
    print(f"  placeholder  : {1 if args.placeholder else 0}")
    print(f"  class order  : {' '.join(class_order_of(rendered['config']))}")
    print(f"  wrote        : mfcc.c, mfcc.h, ultron_config.h, model_data.h, library.properties")
    print("  next         : ./scripts/deploy_xiao.sh   (compile + flash picks it up)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
