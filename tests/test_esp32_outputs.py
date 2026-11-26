"""Output controller host tests — T15 (spec 003-can-act, FR-009 / FR-017 / NFR-009).

Two layers:

1. ``tests/esp32/outputs_engine_test.cpp`` is compiled with the *same* translation
   unit the firmware ships (``devices/esp32/ultron_voice/outputs.cpp``) and run
   with a fake clock. It asserts the Actuation Behavior Contract timings and
   priority rules — the accepted proof for the behaviours the trained 4-class
   model cannot reach yet (blink 1 s/1 s × 45 s, CP, wiper 45 s, A = 2 s,
   CC = 0.5 s) — plus the edge records the firmware prints as ``DBG out=`` lines
   and the NFR-009 fail-safe. It also asserts the command table really comes from
   the generated registry, not from a second hand-written copy in C.

2. The registry drift check re-reads the generated header
   (``devices/esp32/libraries/UltronVoice/src/ultron_config.h``) and compares
   ``ULTRON_REGISTRY[]`` with ``model/registry.json``, so a registry edit that was
   not regenerated fails here instead of on the bench.

The XIAO's on-device proof (real ``DBG out=`` lines, boot-safe relays) is separate:
the firmware log captured under the device lock, see the T15 PR.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKETCH_DIR = REPO_ROOT / "devices/esp32/ultron_voice"
LIBRARY_SRC = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src"
TEST_SOURCE = REPO_ROOT / "tests/esp32/outputs_engine_test.cpp"
ENGINE_SOURCE = SKETCH_DIR / "outputs.cpp"
CONFIG_HEADER = LIBRARY_SRC / "ultron_config.h"
REGISTRY = REPO_ROOT / "model/registry.json"


def _compiler() -> str:
    for candidate in (os.environ.get("CXX"), "c++", "clang++", "g++"):
        if candidate and shutil.which(candidate):
            return candidate
    pytest.skip("no C++ compiler on PATH (set CXX to override)")


def test_output_engine_host_timings(tmp_path: Path) -> None:
    exe = tmp_path / "outputs_engine_test"
    cmd = [
        _compiler(),
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        f"-I{SKETCH_DIR}",
        f"-I{LIBRARY_SRC}",
        str(TEST_SOURCE),
        str(ENGINE_SOURCE),
        "-o",
        str(exe),
    ]
    build = subprocess.run(cmd, capture_output=True, text=True)
    assert build.returncode == 0, f"compile failed:\n{' '.join(cmd)}\n{build.stderr}"

    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, f"engine test failed:\n{run.stdout}\n{run.stderr}"
    assert "outputs_engine_test: OK" in run.stdout


def _generated_registry() -> list[dict[str, str]]:
    """Parse ULTRON_REGISTRY[] out of the generated config.h (no C compiler)."""
    text = CONFIG_HEADER.read_text(encoding="utf-8")
    marker = "ULTRON_REGISTRY[ULTRON_REGISTRY_SIZE] = {"
    start = text.index(marker)
    body = text[start:].split("};", 1)[0]
    entries = re.findall(
        r'\{\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*\}',
        body,
    )
    return [
        {"id": entry[0], "sigla": entry[1], "behavior": entry[2], "channels": entry[3]}
        for entry in entries
    ]


def test_generated_registry_matches_registry_json() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    expected = [
        {
            "id": str(cmd["id"]),
            "sigla": str(cmd.get("sigla", str(cmd["id"]).upper())),
            "behavior": str(cmd.get("behavior", "")),
            "channels": ",".join(str(ch) for ch in cmd.get("channels", [])),
        }
        for cmd in registry["commands"]
    ]
    generated = _generated_registry()

    assert len(generated) == len(expected), (
        "config.h ULTRON_REGISTRY is out of date — run "
        "`uv run python scripts/gen_esp32_config.py`"
    )
    for got, want in zip(generated, expected):
        assert got == want, (
            "config.h ULTRON_REGISTRY drifted from model/registry.json "
            f"({got} != {want}) — run `uv run python scripts/gen_esp32_config.py`"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
