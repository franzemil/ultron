"""Host-side checks for ``ultron split`` (T9).

pyannote.audio is a heavy hardware/token dep, so it is stubbed.
These tests verify:
  * split module compiles cleanly
  * label validation rejects unknown codes, accepts registry ids
  * split_command slices VAD segments into per-label WAVs
  * produced WAVs pass 16 kHz / 16-bit / mono validation (FR-021)
  * missing HF token fails fast
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


def load_split_module():
    """Import ultron.split with pyannote.audio stubbed."""
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

    fake_pyannote = SimpleNamespace(
        Pipeline=SimpleNamespace(
            from_pretrained=lambda model, use_auth_token=None: _FakePipeline([])
        ),
    )

    with mock.patch.dict(
        sys.modules,
        {
            "pyannote.audio": fake_pyannote,
            "pyannote": SimpleNamespace(audio=fake_pyannote),
        },
    ):
        spec = importlib.util.spec_from_file_location("ultron.split", SPLIT_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, _FakePipeline


@pytest.fixture
def mod():
    return load_split_module()[0]


def test_compiles():
    import py_compile

    py_compile.compile(SPLIT_PY, doraise=True)


def test_validate_label_unknown(mod):
    with pytest.raises(typer.Exit) as exc:
        mod._validate_label("not_a_code", {})
    assert exc.value.exit_code == 2


def test_validate_label_valid(mod):
    registry = {}
    # should not raise
    mod._validate_label("lb", registry)
    mod._validate_label("wake", registry)
    mod._validate_label("noise", registry)


def test_read_wav(mod, tmp_path):
    import wave as wave_mod

    samples = np.zeros(16000, dtype="<i2")
    p = tmp_path / "x.wav"
    with wave_mod.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples.tobytes())

    loaded = mod._read_wav(p)
    assert loaded.shape == (16000,)


def test_split_creates_wavs(mod, tmp_path, monkeypatch):
    """Stubbed pyannote returns 2 segments; split_command writes 2 WAVs."""
    out_root = tmp_path / "dataset"

    # Build a fake take WAV with 3 seconds of audio
    take = tmp_path / "take_01_lb.wav"
    take_samples = np.zeros(mod.SAMPLE_RATE * 3, dtype="<i2")
    with wave.open(str(take), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(mod.SAMPLE_RATE)
        w.writeframes(take_samples.tobytes())

    # Fake pipeline: segments at 0.5-1.0s and 1.5-2.0s
    from unittest import mock as _mock

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

    with _mock.patch.dict(sys.modules, {"pyannote.audio": fake_pyannote, "pyannote": SimpleNamespace(audio=fake_pyannote)}):
        # Re-load module to pick up the new fake pipeline
        import importlib.util as _iu
        spec = _iu.spec_from_file_location("ultron.split2", SPLIT_PY)
        m2 = _iu.module_from_spec(spec)
        spec.loader.exec_module(m2)

        with monkeypatch.context() as m:
            m.setenv("PYANNOTE_HF_TOKEN", "fake-token")
            m2.split_command(str(take), "lb", hf_token="fake-token", out_dir=str(out_root))

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


def test_split_no_speech_exits_nonzero(mod, tmp_path, monkeypatch):
    take = tmp_path / "take_01_lb.wav"
    with wave.open(str(take), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(mod.SAMPLE_RATE)
        w.writeframes(np.zeros(mod.SAMPLE_RATE * 2, dtype="<i2").tobytes())

    class _FakeTimeline:
        def __init__(self, segments):
            self._segments = segments
        def support(self):
            return self._segments

    class _FakeVadResult:
        def get_timeline(self):
            return _FakeTimeline([])

    class _FakePipeline:
        def __call__(self, waveform):
            return _FakeVadResult()

    fake_pyannote = SimpleNamespace(
        Pipeline=SimpleNamespace(from_pretrained=lambda model, use_auth_token=None: _FakePipeline())
    )

    from unittest import mock as _mock
    with _mock.patch.dict(sys.modules, {"pyannote.audio": fake_pyannote, "pyannote": SimpleNamespace(audio=fake_pyannote)}):
        import importlib.util as _iu
        spec = _iu.spec_from_file_location("ultron.split3", SPLIT_PY)
        m3 = _iu.module_from_spec(spec)
        spec.loader.exec_module(m3)

        with monkeypatch.context() as m:
            m.setenv("PYANNOTE_HF_TOKEN", "fake-token")
            with pytest.raises(typer.Exit) as exc:
                m3.split_command(str(take), "lb", hf_token="fake-token", out_dir=str(tmp_path / "dataset"))
            assert exc.value.exit_code == 1


def test_split_missing_hf_token_fails_fast(mod, tmp_path, monkeypatch):
    take = tmp_path / "take_01_lb.wav"
    with wave.open(str(take), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(mod.SAMPLE_RATE)
        w.writeframes(np.zeros(mod.SAMPLE_RATE * 2, dtype="<i2").tobytes())

    with monkeypatch.context() as m:
        m.delenv("PYANNOTE_HF_TOKEN", raising=False)
        with pytest.raises(typer.Exit) as exc:
            mod.split_command(str(take), "lb", hf_token=None, out_dir=str(tmp_path / "dataset"))
        assert exc.value.exit_code == 2
