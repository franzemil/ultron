"""Host-side checks for ``ultron split`` (T9 — Silero VAD).

silero-vad is stubbed. These tests verify:
  * split module compiles cleanly
  * label validation rejects unknown codes, accepts registry ids
  * split_command slices VAD segments into per-label WAVs
  * produced WAVs pass 16 kHz / 16-bit / mono validation (FR-021)
  * no-speech take exits non-zero

Run:  uv run pytest tests/test_split.py
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

SPLIT_PY = Path(__file__).resolve().parents[1] / "src" / "ultron" / "split.py"


def make_fake_silero(segments_ms):
    """Build a fake silero_vad module returning fixed segments.

    segments_ms: list of (start_ms, end_ms) tuples.
    """
    def fake_get_speech_timestamps(samples, model, sampling_rate=16000, **kwargs):
        out = []
        for start_ms, end_ms in segments_ms:
            out.append({
                "start": int(start_ms * sampling_rate / 1000),
                "end": int(end_ms * sampling_rate / 1000),
            })
        return out

    return SimpleNamespace(
        load_silero_vad=lambda: object(),
        get_speech_timestamps=fake_get_speech_timestamps,
    )


def load_split_module(fake_silero):
    with mock.patch.dict(sys.modules, {"silero_vad": fake_silero}):
        spec = importlib.util.spec_from_file_location("ultron.split", SPLIT_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def make_take(tmp_path, seconds=3.0):
    """Create a fake take WAV of `seconds` length."""
    take = tmp_path / "take_01_lb.wav"
    with wave.open(str(take), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(int(16000 * seconds), dtype="<i2").tobytes())
    return take


def test_compiles():
    import py_compile

    py_compile.compile(SPLIT_PY, doraise=True)


def test_validate_label_unknown():
    mod = load_split_module(make_fake_silero([]))
    with pytest.raises(typer.Exit) as exc:
        mod._validate_label("not_a_code", {})
    assert exc.value.exit_code == 2


def test_validate_label_valid():
    mod = load_split_module(make_fake_silero([]))
    mod._validate_label("lb", {})
    mod._validate_label("wake", {})
    mod._validate_label("noise", {})


def test_read_wav(tmp_path):
    mod = load_split_module(make_fake_silero([]))
    samples = np.zeros(16000, dtype="<i2")
    p = tmp_path / "x.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.tobytes())
    loaded = mod._read_wav(p)
    assert loaded.shape == (16000,)


def test_split_creates_wavs(tmp_path):
    """Silero VAD returns 2 segments; split_command writes 2 WAVs."""
    fake_silero = make_fake_silero([(500, 1000), (1500, 2000)])
    mod = load_split_module(fake_silero)

    take = make_take(tmp_path, seconds=3.0)
    out_root = tmp_path / "dataset"

    with mock.patch.dict(sys.modules, {"silero_vad": fake_silero}):
        mod.split_command(str(take), "lb", out_dir=str(out_root))

    label_dir = out_root / "originals" / "lb"
    files = sorted(label_dir.glob("lb_local_*.wav")) if label_dir.is_dir() else []
    assert len(files) == 2, files
    assert [p.name for p in files] == ["lb_local_01.wav", "lb_local_02.wav"]

    for wav in files:
        with wave.open(str(wav), "rb") as w:
            assert w.getframerate() == 16000
            assert w.getsampwidth() == 2
            assert w.getnchannels() == 1
            assert w.getnframes() > 0


def test_split_no_speech_exits_nonzero(tmp_path):
    fake_silero = make_fake_silero([])
    mod = load_split_module(fake_silero)

    take = make_take(tmp_path, seconds=2.0)
    with mock.patch.dict(sys.modules, {"silero_vad": fake_silero}):
        with pytest.raises(typer.Exit) as exc:
            mod.split_command(str(take), "lb", out_dir=str(tmp_path / "dataset"))
    assert exc.value.exit_code == 1


def test_split_skips_tiny_segments(tmp_path):
    """Segments shorter than 50 ms are skipped."""
    fake_silero = make_fake_silero([(0, 20), (500, 1000)])  # 20 ms + 500 ms
    mod = load_split_module(fake_silero)

    take = make_take(tmp_path, seconds=3.0)
    out_root = tmp_path / "dataset"

    with mock.patch.dict(sys.modules, {"silero_vad": fake_silero}):
        mod.split_command(str(take), "lb", out_dir=str(out_root))

    label_dir = out_root / "originals" / "lb"
    files = sorted(label_dir.glob("lb_local_*.wav")) if label_dir.is_dir() else []
    assert len(files) == 1, files  # only the 500 ms segment survives
    assert files[0].name == "lb_local_01.wav"
