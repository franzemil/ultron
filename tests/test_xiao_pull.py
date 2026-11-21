"""Tests for the XIAO recorder host tooling — ``devices/esp32/tools/xiao_pull.py``.

The recorder itself needs hardware (XIAO + PDM mic + microSD), but everything the
host side does with the bytes is testable without a board:

* ``wav_duration`` parses the recorder's WAV payloads (rate / width / channels /
  length) — the check that catches a recorder that silently wrote the wrong
  format (US-002 AC1);
* ``save_wav`` writes the pulled payload to disk and re-opens it with the stdlib
  ``wave`` module, so a pulled clip is validated twice;
* the CLI surface (subcommands + the trained-label default) is pinned so the
  documented T28 commands cannot drift silently.

What is *not* mocked: the serial transport. Talking to the device is exercised
by the on-device pass in ``docs/recording.md``, not by CI.
"""

import importlib.util
import pathlib
import struct
import wave

import pytest

_PULL_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "devices" / "esp32" / "tools" / "xiao_pull.py"
)
_spec = importlib.util.spec_from_file_location("xiao_pull", _PULL_PATH)
xiao_pull = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xiao_pull)


def make_wav_bytes(
    seconds: float = 5.0,
    rate: int = 16000,
    channels: int = 1,
    sampwidth: int = 2,
    fill: int = 0,
) -> bytes:
    """Build the byte payload the recorder serves over ``get <name>``."""
    n_frames = int(rate * seconds)
    data_size = n_frames * channels * sampwidth
    block_align = channels * sampwidth
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, rate * block_align, block_align, sampwidth * 8
    )
    header += b"data" + struct.pack("<I", data_size)
    body = bytes([fill]) * data_size
    return header + body


def test_wav_duration_reads_the_recorder_header():
    rate, sampwidth, channels, seconds = xiao_pull.wav_duration(make_wav_bytes(5.0))
    assert (rate, sampwidth, channels) == (16000, 2, 1)
    assert seconds == pytest.approx(5.0)


def test_save_wav_writes_a_playable_file(tmp_path):
    payload = make_wav_bytes(5.0, fill=7)
    path = xiao_pull.save_wav(str(tmp_path), "la_xiao_01.wav", payload)
    assert pathlib.Path(path).read_bytes() == payload
    with wave.open(path, "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 16000 * 5


def test_save_wav_rejects_a_wrong_format(tmp_path):
    """A stereo/8 kHz clip must fail loudly — never be accepted silently."""
    stereo = make_wav_bytes(1.0, channels=2)
    with pytest.raises(xiao_pull.RecorderError, match="expected 16000 Hz"):
        xiao_pull.save_wav(str(tmp_path), "bad_xiao_01.wav", stereo)
    low_rate = make_wav_bytes(1.0, rate=8000)
    with pytest.raises(xiao_pull.RecorderError, match="expected 16000 Hz"):
        xiao_pull.save_wav(str(tmp_path), "bad_xiao_02.wav", low_rate)


def test_wav_duration_rejects_non_wav_payloads():
    with pytest.raises(xiao_pull.RecorderError, match="too short"):
        xiao_pull.wav_duration(b"RIFF")
    with pytest.raises(xiao_pull.RecorderError, match="not a RIFF/WAVE"):
        xiao_pull.wav_duration(b"XXXX" + b"\x00" * 60)


def test_cli_session_defaults_are_the_trained_labels():
    """The T28 pass records exactly the trained subset (spec 002 TD-012)."""
    args = xiao_pull.build_parser().parse_args(["session"])
    assert args.func is xiao_pull.cmd_session
    assert args.labels == "wake,noise,la,lb"
    assert args.takes >= 1


def test_cli_pull_and_rec_subcommands_exist():
    parser = xiao_pull.build_parser()
    assert parser.parse_args(["ls"]).func is xiao_pull.cmd_ls
    rec = parser.parse_args(["rec", "lb", "--takes", "2", "--out", "/tmp/x"])
    assert rec.func is xiao_pull.cmd_rec
    assert rec.label == "lb" and rec.takes == 2
    pull = parser.parse_args(["pull", "--out", "/tmp/x", "--prefix", "wake_"])
    assert pull.func is xiao_pull.cmd_pull and pull.prefix == "wake_"
