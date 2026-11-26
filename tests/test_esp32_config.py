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
CONFIG = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src/ultron_config.h"


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
    )
    assert read_config() == expected


def test_thresholds_are_not_hardcoded_in_c_sources():
    """The gates must come from the generated header only (T12 checklist)."""
    text = read_config()
    assert "GENERATED FILE, DO NOT EDIT" in text
    assert "ULTRON_WAKE_THRESHOLD" in text
    for src in (REPO_ROOT / "devices/esp32/libraries/UltronVoice/src").glob("*.c"):
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
        )


def test_missing_thresholds_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="thresholds.json not found"):
        load_thresholds(tmp_path / "nope.json")


def test_library_mfcc_copies_are_byte_identical():
    """The library vendors dsp/mfcc.c; drift must fail loudly (same bytes as the host)."""
    from scripts.sync_ultronvoice import main as sync_main

    assert sync_main(["--check"]) == 0


# ── library versioning (scripts/gen_esp32_library.py) ────────────────────────


def test_library_version_is_in_the_generated_header():
    """A flashed board must be able to say which library/model it is running."""
    from scripts.gen_esp32_config import read_library_version

    version = read_library_version()
    assert re.match(r"^\d+\.\d+\.\d+$", version), version
    assert f'#define ULTRON_LIBRARY_VERSION "{version}"' in read_config()
    assert f"library version: {version}" in read_config()


def test_artifacts_are_current():
    """--check: the committed library agrees with model/ (deploy refuses to flash otherwise)."""
    from scripts.gen_esp32_library import main as library_main

    assert library_main(["--check"]) == 0


def test_bump_rules_are_patch_and_minor():
    from ultron.esp32lib import bump_version

    assert bump_version("0.1.0", "patch") == "0.1.1"
    assert bump_version("0.1.9", "patch") == "0.1.10"
    assert bump_version("0.1.4", "minor") == "0.2.0"
    assert bump_version("3.7.2", "minor") == "3.8.0"


def test_library_properties_has_a_semver():
    from ultron.esp32lib import read_version

    assert re.match(r"^\d+\.\d+\.\d+$", read_version())


# ── `ultron esp32 lib` (cli/src/ultron/esp32lib.py) ──────────────────────────


def test_cli_command_is_registered():
    from typer.testing import CliRunner

    from ultron.cli import app

    runner = CliRunner()
    for args in (["esp32", "--help"], ["esp32", "lib", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
    assert "lib" in runner.invoke(app, ["esp32", "--help"]).output


def test_cli_reports_version_and_paths():
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(app, ["esp32", "lib"])
    assert result.exit_code == 0, result.output
    assert "UltronVoice v" in result.output
    assert "--check" in result.output and "--sync" in result.output


def test_cli_check_passes_on_a_clean_tree():
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(app, ["esp32", "lib", "--check"])
    assert result.exit_code == 0, result.output
    assert "no drift" in result.output


def test_refresh_is_idempotent_when_nothing_changed():
    """The refresh must be a no-op (no writes, no bump) when artifacts already match."""
    from ultron import esp32lib

    model = esp32lib.REPO_ROOT / "build/placeholder/model_int8.tflite"
    if not model.is_file():
        pytest.skip("placeholder flatbuffer not built (gitignored build/ artifact)")
    before = esp32lib.read_version()
    result = esp32lib.refresh(model=model, placeholder=True, bump=True)
    assert result.changed_files == []
    assert result.bumped is False
    assert esp32lib.read_version() == before


def test_refresh_bumps_minor_on_class_order_change(tmp_path):
    """A frozen class-order change is a contract change: minor bump."""
    from ultron import esp32lib

    assert esp32lib.bump_version("0.1.4", "minor") == "0.2.0"
    # class_order_of() is the single reader used by the bump decision
    config = (esp32lib.LIB_SRC / "ultron_config.h").read_text()
    order = esp32lib._class_order_of(config)
    assert order[:2] == ["wake", "noise"]
