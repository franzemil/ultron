"""Host-side checks for ``ultron record-split`` (T9).

Combines record-long + split in one command. sounddevice / pyannote stubbed.
Verifies:
  * record-split module wiring (record_long_command then split_command)
  * unknown label rejected
  * end-to-end: records a take, splits into segments, writes per-label WAVs

Run:  uv run pytest tests/test_record_split.py
"""

from __future__ import annotations

import importlib.util
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest
import typer
import questionary
from ultron.record_long import SAMPLE_RATE

CLI_PY = Path(__file__).resolve().parents[1] / "src" / "ultron" / "cli.py"


def load_cli_module():
    """Import ultron.cli with sounddevice/pyannote stubbed."""
    fake_sd = SimpleNamespace(
        query_devices=lambda: [
            {"index": 0, "name": "Built-in Microphone", "max_input_channels": 1},
        ],
        rec=lambda *a, **k: None,
        wait=lambda: None,
        default=SimpleNamespace(device=(0, None)),
    )
    with mock.patch.dict(sys.modules, {"sounddevice": fake_sd, "numpy": np}):
        spec = importlib.util.spec_from_file_location("ultron.cli", CLI_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture
def mod():
    return load_cli_module()


def test_compiles():
    import py_compile

    py_compile.compile(CLI_PY, doraise=True)


def test_record_split_unknown_label(mod):
    """Unknown label should fail fast (no recording, no split)."""
    with pytest.raises(typer.Exit) as exc:
        mod.record_split_command(seconds=2, label="not_a_code", device=None, out_dir="/tmp/ds")
    assert exc.value.exit_code == 2


def test_record_split_picker(mod, tmp_path, monkeypatch):
    """When label is omitted, questionary picker is used to select a code."""
    out_root = tmp_path / "dataset"
    monkeypatch.setenv("PYANNOTE_HF_TOKEN", "fake-token")

    # Stub questionary.select to return "wake"
    class _FakeSelectResult:
        def ask(self):
            return "wake"

    def fake_select(prompt, choices=None, **kwargs):
        return _FakeSelectResult()

    fake_recording = np.zeros(SAMPLE_RATE * 3, dtype="<i2")

    class _FakeTimeline:
        def __init__(self, segments):
            self._segments = segments
        def support(self):
            return self._segments

    class _FakeVadResult:
        def __init__(self, segments):
            self._timeline = _FakeTimeline(segments)
        def get_timeline(self):
            return self._timeline

    class _FakePipeline:
        def __init__(self, segments):
            self._segments = segments
        def __call__(self, waveform):
            return _FakeVadResult(self._segments)

    fake_pipeline = _FakePipeline([(0.5, 1.0)])
    fake_pyannote = SimpleNamespace(
        Pipeline=SimpleNamespace(from_pretrained=lambda model, use_auth_token=None: fake_pipeline)
    )

    def fake_rec(*args, **kwargs):
        return fake_recording

    with mock.patch.dict(sys.modules, {
        "pyannote.audio": fake_pyannote,
        "pyannote": SimpleNamespace(audio=fake_pyannote),
    }),          mock.patch.object(mod.questionary, "select", side_effect=fake_select),          mock.patch.object(__import__("ultron.record_long", fromlist=["sd"]).sd, "rec", fake_rec),          mock.patch.object(__import__("ultron.record_long", fromlist=["sd"]).sd, "wait"):
        mod.record_split_command(seconds=3, label=None, device=None, hf_token="tok", out_dir=str(out_root))

    # Should have recorded and split for "wake"
    takes = sorted(out_root.glob("takes/take_*_wake.wav"))
    assert len(takes) == 1, takes

    segments = sorted(out_root.glob("originals/wake/wake_local_*.wav"))
    assert len(segments) == 1, segments
    assert segments[0].name == "wake_local_01.wav"

    for wav in segments:
        with wave.open(str(wav), "rb") as w:
            assert w.getframerate() == 16000
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1
            assert w.getnframes() > 0


def test_record_split_end_to_end(mod, tmp_path):
    """record-split records a take then splits into segments."""
    out_root = tmp_path / "dataset"

    # Fake take recording: 3 seconds of audio
    fake_recording = np.zeros(SAMPLE_RATE * 3, dtype="<i2")

    # Fake pyannote: 2 segments
    class _FakeTimeline:
        def __init__(self, segments):
            self._segments = segments

        def support(self):
            return self._segments

    class _FakeVadResult:
        def __init__(self, segments):
            self._timeline = _FakeTimeline(segments)

        def get_timeline(self):
            return self._timeline

    class _FakePipeline:
        def __init__(self, segments):
            self._segments = segments

        def __call__(self, waveform):
            return _FakeVadResult(self._segments)

    fake_pipeline = _FakePipeline([(0.5, 1.0), (1.5, 2.0)])
    fake_pyannote = SimpleNamespace(
        Pipeline=SimpleNamespace(from_pretrained=lambda model, use_auth_token=None: fake_pipeline)
    )

    record_calls = []
    def fake_rec(*args, **kwargs):
        record_calls.append(args)
        return fake_recording

    with mock.patch.dict(sys.modules, {"pyannote.audio": fake_pyannote, "pyannote": SimpleNamespace(audio=fake_pyannote)}), \
         mock.patch.object(__import__("ultron.record_long", fromlist=["sd"]).sd, "rec", fake_rec), \
         mock.patch.object(__import__("ultron.record_long", fromlist=["sd"]).sd, "wait"):
        mod.record_split_command(seconds=3, label="lb", device=None, hf_token="tok", out_dir=str(out_root))

    # A take was recorded
    takes = sorted(out_root.glob("takes/take_*_lb.wav"))
    assert len(takes) == 1, takes

    # Segments were split
    segments = sorted(out_root.glob("originals/lb/lb_local_*.wav"))
    assert len(segments) == 2, segments
    assert [p.name for p in segments] == ["lb_local_01.wav", "lb_local_02.wav"]

    # Each segment is valid 16k/16-bit/mono
    for wav in segments:
        with wave.open(str(wav), "rb") as w:
            assert w.getframerate() == 16000
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1
            assert w.getnframes() > 0
