"""Tests for `ultron split` (spec 001 T9) — take → per-utterance clips.

Host-side only: the Silero VAD is stubbed, so nothing here needs the model,
a microphone, or torch. What is covered is the part that actually broke:
**where the segments land** (`<dataset root>/originals/<label>/`, never a
doubled `originals/originals/<label>`), plus numbering, label validation and
the no-speech exit path.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
import typer

import ultron.dataset as dataset_mod
import ultron.split as split_mod

SAMPLE_RATE = 16000


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_take(path: Path, seconds: float = 2.0) -> Path:
    """Write a 16 kHz / 16-bit / mono take WAV."""
    n = int(seconds * SAMPLE_RATE)
    samples = (np.sin(np.arange(n) / 8.0) * 8000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())
    return path


def _stub_vad(monkeypatch, segments):
    """Replace Silero with a fixed list of (start, end) sample pairs."""
    timestamps = [{"start": s, "end": e} for s, e in segments]

    def fake_get_speech_timestamps(samples, model, **kwargs):  # noqa: ANN001, ARG001
        return timestamps

    monkeypatch.setattr(split_mod, "_load_vad", lambda: (None, fake_get_speech_timestamps))
    return timestamps


def _split(take: Path, label: str, out_dir: Path) -> None:
    split_mod.split_command(str(take), label, out_dir=str(out_dir))


# ── dataset root resolution (the 2026-11-18 regression) ─────────────────────


def test_check_dataset_root_accepts_a_dataset_root(tmp_path):
    root = tmp_path / "dataset"
    assert dataset_mod.check_dataset_root(root) == root
    assert dataset_mod.resolve_label_dir(root, "lb") == root / "originals" / "lb"


def test_check_dataset_root_refuses_a_doubled_originals_path(tmp_path):
    """Passing `dataset/originals` is a loud error, not a second tree."""
    doubled = tmp_path / "dataset" / "originals"
    with pytest.raises(ValueError) as excinfo:
        dataset_mod.check_dataset_root(doubled)
    message = str(excinfo.value)
    assert "pass the dataset root" in message
    assert "invisible to features/train" in message


def test_split_writes_under_originals_label_once(tmp_path, monkeypatch):
    """Segments land in <root>/originals/<label>/ — no originals/originals."""
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 8000), (9600, 16000)])
    root = tmp_path / "dataset"

    _split(take, "lb", root)

    label_dir = root / "originals" / "lb"
    written = sorted(p.name for p in label_dir.glob("*.wav"))
    assert written == ["lb_local_01.wav", "lb_local_02.wav"]
    assert not (root / "originals" / "originals").exists()


def test_split_cli_default_out_dir_is_the_dataset_root(tmp_path, monkeypatch):
    """The command's own default must match its `<root>/originals/<label>` join."""
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 8000)])
    monkeypatch.chdir(tmp_path)

    split_mod.split_command(str(take), "lb")

    assert (tmp_path / "dataset" / "originals" / "lb" / "lb_local_01.wav").is_file()
    assert not (tmp_path / "dataset" / "originals" / "originals").exists()


def test_split_refuses_doubled_out_dir_and_writes_nothing(tmp_path, monkeypatch):
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 8000)])
    root = tmp_path / "dataset"

    with pytest.raises(typer.Exit) as excinfo:
        _split(take, "lb", root / "originals")

    assert excinfo.value.exit_code == 2
    assert not (root / "originals").exists()  # nothing was created


# ── numbering ───────────────────────────────────────────────────────────────


def test_split_continues_numbering_without_clobbering(tmp_path, monkeypatch):
    root = tmp_path / "dataset"
    label_dir = root / "originals" / "lb"
    label_dir.mkdir(parents=True)
    for nn in (2, 3, 10):
        _write_take(label_dir / f"lb_local_{nn:02d}.wav", seconds=0.5)
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 8000), (9600, 16000)])

    _split(take, "lb", root)

    assert sorted(p.name for p in label_dir.glob("*.wav")) == [
        "lb_local_02.wav",
        "lb_local_03.wav",
        "lb_local_10.wav",
        "lb_local_11.wav",
        "lb_local_12.wav",
    ]


def test_next_nn_is_one_past_the_highest(tmp_path):
    label_dir = tmp_path / "lb"
    label_dir.mkdir()
    assert split_mod._next_nn(label_dir, "lb") == 1
    for nn in (1, 7, 3):
        (label_dir / f"lb_local_{nn:02d}.wav").write_bytes(b"")
    assert split_mod._next_nn(label_dir, "lb") == 8


# ── validation and failure paths ────────────────────────────────────────────


def test_split_rejects_unknown_label_before_loading_vad(tmp_path, monkeypatch):
    take = _write_take(tmp_path / "take_01_lb.wav")
    loaded = []
    monkeypatch.setattr(split_mod, "_load_vad", lambda: loaded.append(True))

    with pytest.raises(typer.Exit) as excinfo:
        _split(take, "not_a_code", tmp_path / "dataset")

    assert excinfo.value.exit_code == 2
    assert loaded == []  # no VAD work for a rejected label


def test_split_fails_on_missing_take(tmp_path, monkeypatch):
    monkeypatch.setattr(split_mod, "_load_vad", lambda: pytest.fail("VAD must not load"))
    with pytest.raises(typer.Exit) as excinfo:
        _split(tmp_path / "absent.wav", "lb", tmp_path / "dataset")
    assert excinfo.value.exit_code == 2


def test_split_exits_one_when_no_speech(tmp_path, monkeypatch):
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [])

    with pytest.raises(typer.Exit) as excinfo:
        _split(take, "lb", tmp_path / "dataset")

    assert excinfo.value.exit_code == 1
    assert not (tmp_path / "dataset" / "originals" / "lb").exists()


def test_split_skips_segments_shorter_than_50ms(tmp_path, monkeypatch):
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 400), (9600, 16000)])  # 25 ms, then 400 ms

    _split(take, "lb", tmp_path / "dataset")

    written = sorted(p.name for p in (tmp_path / "dataset" / "originals" / "lb").glob("*.wav"))
    assert written == ["lb_local_01.wav"]  # numbering skips the dropped segment


def test_split_output_is_16k_16bit_mono(tmp_path, monkeypatch):
    take = _write_take(tmp_path / "take_01_lb.wav")
    _stub_vad(monkeypatch, [(0, 8000)])

    _split(take, "lb", tmp_path / "dataset")

    out = tmp_path / "dataset" / "originals" / "lb" / "lb_local_01.wav"
    with wave.open(str(out), "rb") as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (16000, 2, 1)
        assert w.getnframes() == 8000
