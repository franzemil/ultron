"""Guards for the firmware's generated config — spec 003-can-act T12.

``devices/esp32/ultron_voice/config.h`` is generated from ``model/thresholds.json``
(spec 002 Q-006) + ``model/registry.json`` (spec 001 T3) by
``scripts/gen_esp32_config.py`` and committed so a clean checkout compiles. These
tests fail loudly if the committed header drifts from its sources — the operating
point must never be edited by hand in C (T12 checklist), and the firmware must
never silently keep an old threshold after a retrain.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.gen_esp32_config import (
    ConfigError,
    DEFAULT_OUT,
    DEFAULT_REGISTRY,
    DEFAULT_THRESHOLDS,
    generate,
    main,
    resolve_class_order,
    load_registry,
    load_thresholds,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "devices/esp32/ultron_voice/config.h"


def read_config() -> str:
    assert CONFIG.is_file(), f"{CONFIG} is missing — run scripts/gen_esp32_config.py"
    return CONFIG.read_text(encoding="utf-8")


def test_committed_header_is_up_to_date(capsys):
    """--check re-renders the header and fails on any drift (CI guard)."""
    rc = main(["--check", "--labels", str(REPO_ROOT / "build/labels.json")])
    out = capsys.readouterr()
    assert rc == 0, (out.out + out.err)


def test_header_matches_generator_output():
    """Belt and braces: the file bytes equal what the generator renders today."""
    expected = generate(
        out_path=CONFIG,
        thresholds_path=DEFAULT_THRESHOLDS,
        registry_path=DEFAULT_REGISTRY,
        labels_path=REPO_ROOT / "build/labels.json",
        train_report_path=REPO_ROOT / "build/train_report.json",
    )
    assert read_config() == expected


def test_thresholds_are_not_hardcoded_in_c_sources():
    """The gates must come from the generated header only (T12 checklist)."""
    text = read_config()
    assert "GENERATED FILE, DO NOT EDIT" in text
    assert "ULTRON_WAKE_THRESHOLD" in text
    for src in (REPO_ROOT / "devices/esp32/ultron_voice").glob("*.c"):
        body = src.read_text(encoding="utf-8")
        assert "0.8f" not in body, f"{src.name} hardcodes a threshold literal"
    for src in (REPO_ROOT / "devices/esp32/ultron_voice").glob("*.ino"):
        body = src.read_text(encoding="utf-8")
        assert "0.8f" not in body, f"{src.name} hardcodes a threshold literal"


def test_window_and_wake_gate_come_from_thresholds_json():
    thresholds = load_thresholds(DEFAULT_THRESHOLDS)
    text = read_config()
    window_ms = int(round(float(thresholds["window_s"]) * 1000))
    assert f"#define ULTRON_WINDOW_MS {window_ms}u" in text
    assert f"#define ULTRON_WAKE_THRESHOLD {thresholds['wake']:.1f}f" in text
    assert f"#define ULTRON_WAKE_MARGIN {thresholds['margin_wake']:.1f}f" in text


def test_every_trained_command_is_in_the_header():
    thresholds = load_thresholds(DEFAULT_THRESHOLDS)
    text = read_config()
    for cmd_id, gate in thresholds["commands"].items():
        assert f'"{cmd_id}"' in text
        assert f"{gate['threshold']:.1f}f" in text
    assert f"#define ULTRON_NUM_COMMANDS {len(thresholds['commands'])}" in text


def test_class_order_starts_with_wake_and_noise():
    """Spec 002 TD-012: wake and noise are always present, and first."""
    match = re.search(r"ULTRON_CLASS_NAMES\[[^\]]*\]\s*=\s*\{([^}]*)\}", read_config())
    assert match, "ULTRON_CLASS_NAMES not found"
    names = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
    assert names[:2] == ["wake", "noise"], names
    assert len(names) >= 3


def test_registry_channels_are_carried_for_the_output_layer():
    """T15 consumes behavior/channels from the same generated header."""
    registry = load_registry(DEFAULT_REGISTRY)
    by_id = {cmd["id"]: cmd for cmd in registry["commands"]}
    text = read_config()
    for cmd_id in load_thresholds(DEFAULT_THRESHOLDS)["commands"]:
        channels = ",".join(by_id[cmd_id]["channels"])
        assert f'"{channels}"' in text
        assert f'"{by_id[cmd_id]["behavior"]}"' in text


def test_unknown_trained_command_fails_loudly(tmp_path):
    """A thresholds entry the registry does not define is a spec violation."""
    bad = json.loads(DEFAULT_THRESHOLDS.read_text(encoding="utf-8"))
    bad["commands"]["nope"] = {"threshold": 0.9, "margin": 0.05}
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError, match="registry does not"):
        resolve_class_order(
            load_thresholds(path),
            load_registry(DEFAULT_REGISTRY),
            labels_path=None,
            train_report_path=None,
        )


def test_missing_thresholds_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="thresholds.json not found"):
        load_thresholds(tmp_path / "nope.json")
