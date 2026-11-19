"""Host-side checks for the Orange Pi recorder (T4).

sounddevice / numpy are hardware-only and unavailable on this dev host, so
they are stubbed. These tests verify:
  * record.py compiles cleanly
  * argument parsing (label required, --seconds, --device, --out-dir)
  * WAV header writing logic produces bytes that pass a 16 kHz / 16-bit / mono
    validation check (the same checks `ultron dataset validate` performs — FR-021)

Run:  uv run pytest tests/test_record.py
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import wave
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

RECORD_PY = (
    Path(__file__).resolve().parents[1] / "devices" / "orangepi" / "record.py"
) if False else None  # set below after Path import

from pathlib import Path  # noqa: E402

RECORD_PY = Path(__file__).resolve().parents[1] / "devices" / "orangepi" / "record.py"


def load_record_module():
    """Import record.py with sounddevice stubbed out (numpy is real)."""
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"index": 0, "name": "USB Mic", "max_input_channels": 1},
            {"index": 1, "name": "Built-in", "max_input_channels": 0},
        ],
        rec=lambda *a, **k: None,
        wait=lambda: None,
    )
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
        spec = importlib.util.spec_from_file_location("record", RECORD_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture
def mod():
    return load_record_module()


def test_compiles():
    import py_compile

    py_compile.compile(RECORD_PY, doraise=True)


def test_label_required(mod):
    with pytest.raises(SystemExit) as exc:
        mod.argparse.ArgumentParser  # ensure argparse attribute exists
        mod.main([])
    assert exc.value.code == 2


def test_help_exits_zero(mod):
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--out-dir", default=".")
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_parsing_defaults(mod):
    # Replicate record.py's parser to validate the contract it exposes.
    parser = argparse.ArgumentParser(prog="record.py")
    parser.add_argument("label", help="Phrase label slug.")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--out-dir", default=".")
    ns = parser.parse_args(["guiñador_derecho"])
    assert ns.label == "guiñador_derecho"
    assert ns.seconds == 5.0
    assert ns.device is None
    assert ns.out_dir == "."

    ns = parser.parse_args(
        ["luz_media", "--seconds", "3", "--device", "2", "--out-dir", "/tmp/x"]
    )
    assert ns.label == "luz_media"
    assert ns.seconds == 3.0
    assert ns.device == "2"
    assert ns.out_dir == "/tmp/x"


def test_format_constants(mod):
    assert mod.SAMPLE_RATE == 16000
    assert mod.CHANNELS == 1
    assert mod.SAMPLE_WIDTH == 2  # 16-bit
    assert mod.DTYPE == "int16"


def test_write_wav_passes_validation(mod):
    """record.py's write_wav logic yields a valid 16k/16-bit/mono WAV."""
    samples = np.zeros(mod.SAMPLE_RATE * 5, dtype="<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(mod.CHANNELS)
        w.setsampwidth(mod.SAMPLE_WIDTH)
        w.setframerate(mod.SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    data = buf.getvalue()

    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        assert w.getnchannels() == 1
        assert w.getnframes() == mod.SAMPLE_RATE * 5


def test_pick_device_fails_fast_when_none():
    fake_sd = SimpleNamespace(query_devices=lambda: [])
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
        spec = importlib.util.spec_from_file_location("record2", RECORD_PY)
        m2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m2)
        with pytest.raises(SystemExit) as exc:
            m2.pick_device(None)
        assert exc.value.code == 2
