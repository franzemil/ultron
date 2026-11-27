"""Host tests for the firmware state machine — spec 003-can-act T12.

The firmware's ``state_machine.c`` is plain C99 with no Arduino dependency, so
this test compiles *that file* (plus the *generated* ``config.h``) on the host and
drives it with scripted windows. Every asserted contract line is produced by the
firmware's own ``ultron_sm_format_event()`` — the device prints the same string.

What this proves without hardware (and what it cannot):

* the transition table from design.md → "State machine" (wake gate, command
  gate with threshold + margin over every other class, 5 s timeout, one-time
  Q-005 refresh, playback-overlap discard) behaves as specified — FR-001…FR-008,
  FR-019, FR-037;
* the Target Event Contract text is exactly
  ``EVT WAKE conf=0.94 t_ms=…`` / ``EVT CMD id=lb conf=0.91 t_ms=…`` /
  ``EVT TIMEOUT t_ms=…`` / ``EVT ERROR code=…``;
* it cannot prove that the *model* recognises "hey ultron": with the placeholder
  model, recognition is exactly what is missing (see the PR).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ESP32_DIR = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src"  # the UltronVoice library now owns the state machine
DRIVER = REPO_ROOT / "tests/esp32/sm_host_test.c"
SOURCES = [ESP32_DIR / "ultron_state_machine.c"]

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None, reason="no host C compiler (cc) available"
)


@pytest.fixture(scope="module")
def sm_binary(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sm_host") / "sm_host_test"
    cmd = [
        "cc",
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-O1",
        f"-I{ESP32_DIR}",
        str(DRIVER),
        *[str(src) for src in SOURCES],
        "-o",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"compile failed:\n{proc.stderr}"
    return out


@pytest.fixture(scope="module")
def sm_output(sm_binary: Path) -> str:
    proc = subprocess.run([str(sm_binary)], capture_output=True, text=True)
    assert proc.returncode == 0, (
        "state machine host driver failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "ALL OK" in proc.stdout, proc.stdout
    return proc.stdout


def test_all_scenarios_pass(sm_output: str):
    """The driver asserts every scenario internally; it exits 0 only if all pass."""
    lines = sm_output.splitlines()
    assert not [line for line in lines if line.startswith("FAIL ")], sm_output
    assert "FAILURE(S)" not in sm_output
    assert sm_output.count("OK ") >= 11


@pytest.mark.parametrize(
    "scenario",
    [
        "silence",  # FR-006: below threshold emits nothing
        "wake",  # FR-001/FR-008: wake acceptance
        "wake_below_gate",
        "command_without_wake",  # FR-005: bare commands ignored
        "command",  # FR-003/FR-008: command acceptance
        "command_below_threshold",
        "command_below_margin",
        "timeout",  # FR-004: 5 s window
        "refresh_once",  # Q-005: exactly one refresh
        "playback_overlap",  # FR-019: overlapping windows never decide
        "window_starts_at_playback_end",  # FR-019/NFR-010
        "error",
    ],
)
def test_scenario_named(sm_output: str, scenario: str):
    assert f"OK {scenario}" in sm_output


def test_contract_lines_are_exact(sm_output: str):
    """The design.md event contract, verbatim (design → "Event contract")."""
    assert "EVT WAKE conf=0.94 t_ms=12345" in sm_output
    assert "EVT CMD id=lb conf=0.91 t_ms=5000" in sm_output
    assert "EVT TIMEOUT t_ms=7500" in sm_output
    assert "EVT ERROR code=MIC_INIT_FAILED" in sm_output


def test_generated_config_is_what_the_machine_used(sm_output: str):
    """The driver reads config.h, so its gates are the shipped operating point."""
    import json

    thresholds = json.loads((REPO_ROOT / "model/thresholds.json").read_text())
    window_ms = int(round(thresholds["window_s"] * 1000))
    assert f"window={window_ms}ms" in sm_output
    for cmd_id, gate in thresholds["commands"].items():
        assert f"gate id={cmd_id} " in sm_output
        assert f"thr={gate['threshold']:.2f}+{gate['margin']:.2f}" in sm_output
