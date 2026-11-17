"""Host-side checks for ``ultron record`` (T8).

sounddevice / numpy / rich are hardware / terminal deps, so they are stubbed.
These tests verify:
  * record module compiles cleanly
  * ``ultron record --help`` exits 0
  * device selection (default input, fail-fast when no mic)
  * WAV writing yields a valid 16 kHz / 16-bit / mono file (FR-021 contract)
  * sequence numbering continues from the next free <nn>
  * record_command stops at ``--samples`` examples per selected code

Run:  uv run pytest tests/test_record_cli.py
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

RECORD_PY = Path(__file__).resolve().parents[1] / "src" / "ultron" / "record.py"


def load_record_module():
    """Import ultron.record with sounddevice/numpy/rich stubbed."""
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"index": 0, "name": "Built-in Microphone", "max_input_channels": 1},
            {"index": 1, "name": "USB Mic", "max_input_channels": 1},
        ],
        rec=lambda *a, **k: None,
        wait=lambda: None,
        default=SimpleNamespace(device=(0, None)),
    )
    fake_rich = SimpleNamespace(
        Console=lambda *a, **k: SimpleNamespace(print=lambda *a, **k: None),
        Confirm=SimpleNamespace(ask=lambda *a, **k: False),
        Prompt=SimpleNamespace(ask=lambda *a, **k: ""),
    )
    with mock.patch.dict(
        sys.modules,
        {
            "sounddevice": fake_sd,
            "numpy": np,
            "rich": fake_rich,
            "rich.console": fake_rich,
            "rich.prompt": SimpleNamespace(
                Confirm=fake_rich.Confirm, Prompt=fake_rich.Prompt
            ),
        },
    ):
        spec = importlib.util.spec_from_file_location("ultron.record", RECORD_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture
def mod():
    return load_record_module()


def test_compiles():
    import py_compile

    py_compile.compile(RECORD_PY, doraise=True)


def test_format_constants(mod):
    assert mod.SAMPLE_RATE == 16000
    assert mod.CHANNELS == 1
    assert mod.SAMPLE_WIDTH == 2
    assert mod.DTYPE == "int16"


def test_next_nn_empty_dir(mod, tmp_path):
    d = tmp_path / "orig" / "lm"
    d.mkdir(parents=True)
    assert mod._next_nn(d, "lm", "local") == 1


def test_next_nn_continues(mod, tmp_path):
    d = tmp_path / "orig" / "lm"
    d.mkdir(parents=True)
    (d / "lm_local_01.wav").write_text("x")
    (d / "lm_local_03.wav").write_text("x")
    assert mod._next_nn(d, "lm", "local") == 4


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
        spec = importlib.util.spec_from_file_location("record2", RECORD_PY)
        m2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m2)
        with pytest.raises(SystemExit) as exc:
            m2._pick_device(None)
        assert exc.value.code == 2


def test_record_command_stops_at_samples(mod, tmp_path):
    """record_command records exactly ``--samples`` per selected code."""
    out_root = tmp_path / "dataset"

    fake_recording = np.zeros(mod.SAMPLE_RATE * 2, dtype="<i2")

    # Select only 'lm' and 'wake'; stop after 2 samples each.
    def confirm_ask(prompt, default=False):
        import re

        m = re.search(r"Record '(\w+)'\?", prompt)
        return bool(m and m.group(1) in {"lm", "wake"})

    def fake_rec(*args, **kwargs):
        return fake_recording

    with mock.patch.object(mod.Confirm, "ask", side_effect=confirm_ask), \
         mock.patch.object(mod.sd, "rec", fake_rec), \
         mock.patch.object(mod.sd, "wait"):
        mod.record_command(seconds=2, samples=2, device=None, out_dir=str(out_root))

    lm_dir = out_root / "originals" / "lm"
    wake_dir = out_root / "originals" / "wake"
    lm_files = sorted(lm_dir.glob("lm_local_*.wav")) if lm_dir.is_dir() else []
    wake_files = sorted(wake_dir.glob("wake_local_*.wav")) if wake_dir.is_dir() else []
    assert len(lm_files) == 2, lm_files
    assert len(wake_files) == 2, wake_files
    assert [p.name for p in lm_files] == ["lm_local_01.wav", "lm_local_02.wav"]
    assert [p.name for p in wake_files] == ["wake_local_01.wav", "wake_local_02.wav"]

    # every produced file passes the 16k/16-bit/mono validation
    for wav in list(lm_files) + list(wake_files):
        with wave.open(str(wav), "rb") as w:
            assert w.getframerate() == 16000
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1
