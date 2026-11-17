"""Host-side checks for ``ultron record-long`` (T9).

sounddevice / numpy are hardware deps, so they are stubbed.
These tests verify:
  * record_long module compiles cleanly
  * next take sequencing
  * WAV writing yields valid 16 kHz / 16-bit / mono file
  * record_long_command records and writes a take

Run:  uv run pytest tests/test_record_long.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

RECORD_LONG_PY = Path(__file__).resolve().parents[1] / "src" / "ultron" / "record_long.py"


def load_record_long_module():
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"index": 0, "name": "Built-in Microphone", "max_input_channels": 1},
            {"index": 1, "name": "USB Mic", "max_input_channels": 1},
        ],
        rec=lambda *a, **k: None,
        wait=lambda: None,
        default=SimpleNamespace(device=(0, None)),
    )
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": np}):
        spec = importlib.util.spec_from_file_location("ultron.record_long", RECORD_LONG_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture
def mod():
    return load_record_long_module()


def test_compiles():
    import py_compile

    py_compile.compile(RECORD_LONG_PY, doraise=True)


def test_format_constants(mod):
    assert mod.SAMPLE_RATE == 16000
    assert mod.CHANNELS == 1
    assert mod.SAMPLE_WIDTH == 2
    assert mod.DTYPE == "int16"


def test_next_take_nn_empty_dir(mod, tmp_path):
    d = tmp_path / "takes"
    d.mkdir(parents=True)
    assert mod._next_take_nn(d, "lb") == 1


def test_next_take_nn_continues(mod, tmp_path):
    d = tmp_path / "takes"
    d.mkdir(parents=True)
    (d / "take_01_lb.wav").write_text("x")
    (d / "take_03_lb.wav").write_text("x")
    assert mod._next_take_nn(d, "lb") == 4


def test_next_take_nn_label_specific(mod, tmp_path):
    d = tmp_path / "takes"
    d.mkdir(parents=True)
    (d / "take_01_lb.wav").write_text("x")
    (d / "take_02_wake.wav").write_text("x")
    # lb take sequence is independent of wake take sequence
    assert mod._next_take_nn(d, "lb") == 2
    assert mod._next_take_nn(d, "wake") == 3


def test_write_wav_passes_validation(mod, tmp_path):
    samples = np.zeros(mod.SAMPLE_RATE * 5, dtype="<i2")
    out = tmp_path / "x.wav"
    mod._write_wav(out, samples)
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        assert w.getnchannels() == 1
        assert w.getnframes() == mod.SAMPLE_RATE * 5


def test_pick_device_default_input(mod):
    assert mod._pick_device(None) == 0


def test_pick_device_fail_fast_no_mic():
    fake_sd = SimpleNamespace(
        query_devices=lambda: [],
        default=SimpleNamespace(device=(None, None)),
    )
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sd}):
        spec = importlib.util.spec_from_file_location("rl2", RECORD_LONG_PY)
        m2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m2)
        with pytest.raises(SystemExit) as exc:
            m2._pick_device(None)
        assert exc.value.code == 2


def test_record_long_command_writes_take(mod, tmp_path):
    out_root = tmp_path / "dataset"
    fake_recording = np.zeros(mod.SAMPLE_RATE * 3, dtype="<i2")

    def fake_rec(*args, **kwargs):
        return fake_recording

    with mock.patch.object(mod.sd, "rec", fake_rec), \
         mock.patch.object(mod.sd, "wait"):
        mod.record_long_command(seconds=3, label="lb", device=None, out_dir=str(out_root))

    take_dir = out_root
    files = sorted(take_dir.glob("take_*_lb.wav")) if take_dir.is_dir() else []
    assert len(files) == 1, files
    assert files[0].name == "take_01_lb.wav"

    with wave.open(str(files[0]), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        assert w.getnchannels() == 1
        assert w.getnframes() == mod.SAMPLE_RATE * 3
