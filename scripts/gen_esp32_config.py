#!/usr/bin/env python3
"""Generate ``devices/esp32/ultron_voice/config.h`` from the model metadata.

Task: spec 003-can-act **T12** ("Thresholds + registry injected at build into
`devices/esp32/ultron_voice/config.h`"). Nothing about the operating point may be
hardcoded in C: the thresholds come from ``model/thresholds.json`` (spec 002
Q-006) and the command identity (id, sigla, behavior, channels) from
``model/registry.json`` (spec 001 T3).

Deterministic and committed: the generated header is part of the repo (so a
clean checkout compiles with ``arduino-cli compile`` and nothing is generated
behind the reviewer's back), and ``--check`` re-renders it and fails on any
drift — the same pattern as ``dsp/mfcc.c`` ↔ firmware parity. The CI-guard test
is ``tests/test_esp32_config.py``.

Class order (the "frozen" order the model's output is indexed by, spec 002
TD-012) comes from, in order of precedence:

1. ``--labels PATH`` (``build/labels.json`` written by ``ultron export tflite``)
2. ``build/train_report.json`` → ``class_order`` (if a training run left one)
3. derived: ``wake``, ``noise``, then the *trained* commands in registry order.

Option 3 is what makes the header generatable before the spec-002 artifact
exists; it reproduces ``wake noise lb la`` for the current thresholds file. The
header records which source was used, so a stale class order is visible.

Usage:
    uv run python scripts/gen_esp32_config.py            # write the header
    uv run python scripts/gen_esp32_config.py --check    # fail on drift
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "devices/esp32/ultron_voice/config.h"
DEFAULT_THRESHOLDS = REPO_ROOT / "model/thresholds.json"
DEFAULT_REGISTRY = REPO_ROOT / "model/registry.json"
DEFAULT_LABELS = REPO_ROOT / "build/labels.json"
DEFAULT_TRAIN_REPORT = REPO_ROOT / "build/train_report.json"

# Base classes that every export carries (spec 002 TD-012).
BASE_CLASSES = ("wake", "noise")


class ConfigError(RuntimeError):
    """Loud, actionable failure — never emit a half-valid header."""


def _read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{what} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{what} is not valid JSON: {path} ({exc})") from exc


def load_thresholds(path: Path) -> dict:
    data = _read_json(path, "thresholds.json")
    for key in ("wake", "margin_wake", "commands", "window_s"):
        if key not in data:
            raise ConfigError(f"{path}: missing '{key}' (spec 002 Q-006 schema)")
    if not data["commands"]:
        raise ConfigError(
            f"{path}: no trained commands — the device would have nothing to recognise"
        )
    for cmd_id, gate in data["commands"].items():
        for key in ("threshold", "margin"):
            if key not in gate:
                raise ConfigError(f"{path}: commands.{cmd_id} missing '{key}'")
    return data


def load_registry(path: Path) -> dict:
    data = _read_json(path, "registry.json")
    if "commands" not in data or not data["commands"]:
        raise ConfigError(f"{path}: no commands in the registry")
    return data


def resolve_class_order(
    thresholds: dict,
    registry: dict,
    labels_path: Path | None,
    train_report_path: Path | None,
) -> tuple[list[str], str]:
    """Return (frozen class order, provenance string)."""
    if labels_path is not None and labels_path.is_file():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        if not isinstance(labels, list) or not labels:
            raise ConfigError(f"{labels_path}: expected a non-empty JSON list of labels")
        return [str(x) for x in labels], f"build/labels.json ({labels_path.name})"

    if train_report_path is not None and train_report_path.is_file():
        report = json.loads(train_report_path.read_text(encoding="utf-8"))
        order = report.get("class_order")
        if order:
            return [str(x) for x in order], "build/train_report.json class_order"

    registry_ids = [str(cmd["id"]) for cmd in registry["commands"]]
    trained = set(thresholds["commands"])
    unknown = trained - set(registry_ids)
    if unknown:
        raise ConfigError(
            f"thresholds.json trains command(s) {sorted(unknown)} that the registry does not "
            "define — fix model/thresholds.json or model/registry.json (FR-032)"
        )
    order = list(BASE_CLASSES) + [cid for cid in registry_ids if cid in trained]
    return order, "derived: wake, noise, then trained commands in registry order"


def _c_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _c_string_list(values: list[str], indent: str = "    ") -> str:
    inner = ", ".join(_c_string(v) for v in values)
    return "{" + inner + "}"


def _float(value: float) -> str:
    """Deterministic C float literal (shortest round-tripping repr + f suffix)."""
    text = repr(float(value))
    if text.endswith(".0"):
        text = text[:-2] + ".0"
    return text + "f"


def render(
    thresholds: dict,
    registry: dict,
    class_order: list[str],
    order_source: str,
    source_paths: dict[str, Path],
) -> str:
    trained_ids = list(thresholds["commands"])
    registry_by_id = {str(cmd["id"]): cmd for cmd in registry["commands"]}

    missing = [cid for cid in trained_ids if cid not in class_order]
    if missing:
        raise ConfigError(
            f"trained command(s) {missing} are absent from the frozen class order "
            f"{class_order} — the model's output cannot be indexed by them"
        )
    if class_order[0] != BASE_CLASSES[0] or class_order[1] != BASE_CLASSES[1]:
        raise ConfigError(
            f"frozen class order {class_order} must start with {list(BASE_CLASSES)} "
            "(spec 002 TD-012: wake and noise are always present)"
        )

    digest = hashlib.sha256()
    for key, path in source_paths.items():
        digest.update(key.encode())
        digest.update(path.read_bytes())
    digest.update(json.dumps([class_order, order_source, thresholds, registry]).encode())
    config_sha = digest.hexdigest()

    lines: list[str] = []
    w = lines.append
    w("/*")
    w(" * config.h — GENERATED FILE, DO NOT EDIT.")
    w(" *")
    w(" * Source of truth: model/thresholds.json (spec 002 Q-006) + model/registry.json")
    w(" * (spec 001 T3), rendered by scripts/gen_esp32_config.py for spec 003 T12.")
    w(" * Re-render after any threshold/registry change:")
    w(" *")
    w(" *     uv run python scripts/gen_esp32_config.py")
    w(" *")
    w(" * CI guard: tests/test_esp32_config.py re-renders and fails on drift.")
    w(" *")
    w(f" * config sha256 : {config_sha}")
    w(f" * class order   : {' '.join(class_order)}")
    w(f" * order source  : {order_source}")
    w(f" * window        : {thresholds['window_s']} s")
    w(" */")
    w("")
    w("#ifndef ULTRON_CONFIG_H")
    w("#define ULTRON_CONFIG_H")
    w("")
    w(f'#define ULTRON_CONFIG_SHA256 "{config_sha}"')
    w(f'#define ULTRON_CLASS_ORDER_SOURCE "{order_source}"')
    w("")
    w("/* Frozen class order — the model's output index mapping (spec 002 TD-012). */")
    w(f"#define ULTRON_NUM_CLASSES {len(class_order)}")
    w(f"static const char *const ULTRON_CLASS_NAMES[ULTRON_NUM_CLASSES] = "
      f"{_c_string_list(class_order)};")
    w("")
    w("/* Wake stage (FR-001: wake >= threshold + margin). */")
    w(f"#define ULTRON_WAKE_CLASS {class_order.index(BASE_CLASSES[0])}")
    w(f"#define ULTRON_NOISE_CLASS {class_order.index(BASE_CLASSES[1])}")
    w(f"#define ULTRON_WAKE_THRESHOLD {_float(thresholds['wake'])}")
    w(f"#define ULTRON_WAKE_MARGIN {_float(thresholds['margin_wake'])}")
    w("")
    w("/* Command window (FR-002 / FR-004): 5 s, milliseconds on device. */")
    window_ms = int(round(float(thresholds["window_s"]) * 1000.0))
    w(f"#define ULTRON_WINDOW_MS {window_ms}u")
    w("")
    w(f"/* Trained command subset (spec 002 TD-012) — {len(trained_ids)} command(s). */")
    w(f"#define ULTRON_NUM_COMMANDS {len(trained_ids)}")
    w("static const struct {")
    w("  const char *id;      /* registry id — EVT CMD id=<id>          */")
    w("  const char *sigla;   /* Actuation Behavior Contract sigla      */")
    w("  int class_index;     /* index in the frozen class order        */")
    w("  float threshold;     /* model/thresholds.json commands.<id>    */")
    w("  float margin;        /* model/thresholds.json commands.<id>    */")
    w("  const char *behavior; /* registry enum (T15 consumes)         */")
    w("  const char *channels; /* registry channel list (T15 consumes) */")
    w(f"}} ULTRON_COMMANDS[ULTRON_NUM_COMMANDS] = {{")
    for cid in trained_ids:
        gate = thresholds["commands"][cid]
        entry = registry_by_id[cid]
        channels = ",".join(str(ch) for ch in entry.get("channels", []))
        w("    {")
        w(f"        {_c_string(cid)},")
        w(f"        {_c_string(entry.get('sigla', cid.upper()))},")
        w(f"        {class_order.index(cid)},")
        w(f"        {_float(gate['threshold'])},")
        w(f"        {_float(gate['margin'])},")
        w(f"        {_c_string(entry.get('behavior', ''))},")
        w(f"        {_c_string(channels)},")
        w("    },")
    w("};")
    w("")
    w("#endif /* ULTRON_CONFIG_H */")
    w("")
    return "\n".join(lines)


def generate(
    out_path: Path = DEFAULT_OUT,
    thresholds_path: Path = DEFAULT_THRESHOLDS,
    registry_path: Path = DEFAULT_REGISTRY,
    labels_path: Path | None = DEFAULT_LABELS,
    train_report_path: Path | None = DEFAULT_TRAIN_REPORT,
) -> str:
    thresholds = load_thresholds(thresholds_path)
    registry = load_registry(registry_path)
    class_order, order_source = resolve_class_order(
        thresholds, registry, labels_path, train_report_path
    )
    return render(
        thresholds,
        registry,
        class_order,
        order_source,
        {"thresholds": thresholds_path, "registry": registry_path},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="header to write")
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS,
        help="build/labels.json (frozen class order); falls back to derivation",
    )
    parser.add_argument("--train-report", type=Path, default=DEFAULT_TRAIN_REPORT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed header is stale",
    )
    args = parser.parse_args(argv)

    try:
        text = generate(
            out_path=args.out,
            thresholds_path=args.thresholds,
            registry_path=args.registry,
            labels_path=args.labels,
            train_report_path=args.train_report,
        )
    except ConfigError as exc:
        print(f"gen_esp32_config: {exc}", file=sys.stderr)
        return 2

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
        if current != text:
            print(
                f"gen_esp32_config: {args.out} is stale — run "
                "`uv run python scripts/gen_esp32_config.py`",
                file=sys.stderr,
            )
            return 1
        print(f"gen_esp32_config: {args.out} is up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"gen_esp32_config: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
