"""Tests for `ultron record-long` (spec 001 T9) — the visible countdown.

The point of these tests is the thing that was missing: the owner could not tell
**when to stop speaking**. So the countdown math, the wrap-up flag and the
per-tick rendering are asserted directly, with the clock, the microphone and
rich's Live display stubbed out — no audio device is opened.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
import typer

import ultron.record_long as rl

SAMPLE_RATE = 16000


# ── countdown math (pure) ───────────────────────────────────────────────────


def test_countdown_counts_down_to_one_tick():
    assert list(rl.countdown(0.5, tick=0.1)) == [0.5, 0.4, 0.3, 0.2, 0.1]


def test_countdown_covers_the_whole_duration():
    ticks = list(rl.countdown(20.0, tick=0.1))
    assert len(ticks) == 200  # 20 s at 10 Hz
    assert ticks[0] == pytest.approx(20.0)
    assert ticks[-1] == pytest.approx(0.1)
    assert ticks == sorted(ticks, reverse=True)  # strictly decreasing


def test_countdown_is_float_drift_free():
    """0.1 steps must not drift into 19.899999… style values."""
    ticks = list(rl.countdown(5.0, tick=0.1))
    assert all(t == round(t, 1) for t in ticks)
    assert ticks[0] == 5.0 and ticks[-1] == 0.1


@pytest.mark.parametrize("duration", [0.0, -1.0, -20.0])
def test_countdown_yields_nothing_for_non_positive_duration(duration):
    assert list(rl.countdown(duration)) == []


def test_countdown_never_yields_zero_ticks_for_sub_tick_duration():
    assert list(rl.countdown(0.01, tick=0.1)) == [0.1]


# ── the visible line ────────────────────────────────────────────────────────


def test_progress_line_shows_remaining_seconds_and_bar():
    line = rl.progress_line("lb", 2.5, 20.0)
    assert "recording lb" in line
    assert "2.5/20.0s" in line
    assert "left 17.5s" in line
    assert "wrap up" not in line  # 17.5 s is plenty of time


def test_progress_line_bar_grows_with_elapsed_time():
    empty = rl.progress_line("lb", 0.0, 20.0, width=10)
    half = rl.progress_line("lb", 10.0, 20.0, width=10)
    full = rl.progress_line("lb", 20.0, 20.0, width=10)
    assert "░" * 10 in empty
    assert "█" * 5 + "░" * 5 in half
    assert "█" * 10 in full


def test_progress_line_flags_only_the_last_five_seconds():
    assert "wrap up" not in rl.progress_line("lb", 14.9, 20.0)  # 5.1 s left
    assert "wrap up" in rl.progress_line("lb", 15.0, 20.0)  # 5.0 s left
    assert "wrap up" in rl.progress_line("lb", 19.0, 20.0)  # 1.0 s left


def test_progress_line_clamps_overrun_and_zero_total():
    over = rl.progress_line("lb", 25.0, 20.0, width=8)
    assert "left  0.0s" in over
    assert over.count("█") == 8  # never wider than the bar
    assert "left  0.0s" in rl.progress_line("lb", 0.0, 0.0)


# ── lead-in ─────────────────────────────────────────────────────────────────


def test_lead_in_counts_three_two_one():
    assert rl.lead_in_messages("lb", 3.0) == [
        "Recording 'lb' in 3s — get ready…",
        "Recording 'lb' in 2s — get ready…",
        "Recording 'lb' in 1s — get ready…",
    ]


def test_lead_in_can_be_disabled():
    assert rl.lead_in_messages("lb", 0.0) == []


# ── the render loop ─────────────────────────────────────────────────────────


class _FakeLive:
    """Records `update()` calls instead of drawing to a terminal."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.frames: list[str] = []
        _FakeLive.last = self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, renderable):
        self.frames.append(str(renderable))


def test_recording_progress_ticks_until_done(monkeypatch):
    monkeypatch.setattr(rl, "Live", _FakeLive)
    sleeps: list[float] = []

    rl._run_recording_progress("lb", 0.5, sleep=sleeps.append)

    frames = _FakeLive.last.frames
    assert len(sleeps) == 5  # one sleep per tick
    assert len(frames) == 6  # 5 ticks + the final full-bar frame
    assert all("recording lb" in f for f in frames)
    assert "left  0.1s" in frames[-2]
    assert "0.5/0.5s" in frames[-1] and "left  0.0s" in frames[-1]


def test_recording_progress_does_not_sleep_when_disabled(monkeypatch):
    monkeypatch.setattr(rl, "Live", _FakeLive)
    rl._run_recording_progress("lb", 0.0, sleep=lambda _s: pytest.fail("must not sleep"))
    assert _FakeLive.last.frames == []  # nothing to count down


# ── the command ─────────────────────────────────────────────────────────────


def _stub_audio(monkeypatch):
    calls = {}

    def fake_rec(**kwargs):
        calls["rec"] = kwargs
        return np.zeros((kwargs["frames"], kwargs["channels"]), dtype="int16")

    monkeypatch.setattr(rl.sd, "rec", fake_rec)
    monkeypatch.setattr(rl.sd, "wait", lambda: calls.setdefault("waited", True))
    monkeypatch.setattr(rl, "_pick_device", lambda device: 0)
    monkeypatch.setattr(rl, "time", type("T", (), {"sleep": staticmethod(lambda _s: None)}))
    monkeypatch.setattr(rl, "_run_recording_progress", lambda label, seconds, **kw: calls.setdefault("progress", (label, seconds)))
    return calls


def test_record_long_writes_the_take_and_shows_progress(tmp_path, monkeypatch, capsys):
    calls = _stub_audio(monkeypatch)

    out = rl.record_long_command(
        seconds=2.0, label="lb", device=None, out_dir=str(tmp_path / "takes"), lead_in=0.0
    )

    assert out == tmp_path / "takes" / "take_01_lb.wav"
    with wave.open(str(out), "rb") as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (16000, 2, 1)
        assert w.getnframes() == 2 * SAMPLE_RATE
    assert calls["progress"] == ("lb", 2.0)  # countdown driven for the full take
    assert calls["waited"] is True
    printed = capsys.readouterr().out
    assert "stop speaking when the countdown hits 0" in printed
    assert "get ready" not in printed  # lead-in disabled


def test_record_long_lead_in_prints_countdown(tmp_path, monkeypatch, capsys):
    _stub_audio(monkeypatch)

    rl.record_long_command(
        seconds=1.0, label="wake", device=None, out_dir=str(tmp_path / "takes"), lead_in=3.0
    )

    printed = capsys.readouterr().out
    assert "Recording 'wake' in 3s" in printed
    assert "Recording 'wake' in 2s" in printed
    assert "Recording 'wake' in 1s" in printed


def test_record_long_numbering_continues(tmp_path, monkeypatch):
    _stub_audio(monkeypatch)
    takes = tmp_path / "takes"
    takes.mkdir()
    (takes / "take_01_lb.wav").write_bytes(b"")
    (takes / "take_04_lb.wav").write_bytes(b"")

    out = rl.record_long_command(
        seconds=1.0, label="lb", device=None, out_dir=str(takes), lead_in=0.0
    )

    assert out.name == "take_05_lb.wav"  # one past the highest, never clobbering


def test_record_long_keeps_labels_apart(tmp_path, monkeypatch):
    _stub_audio(monkeypatch)
    takes = tmp_path / "takes"
    takes.mkdir()
    (takes / "take_01_lb.wav").write_bytes(b"")

    out = rl.record_long_command(
        seconds=1.0, label="la", device=None, out_dir=str(takes), lead_in=0.0
    )

    assert out.name == "take_01_la.wav"


# ── direct calls (record-split path) ────────────────────────────────────────


def test_record_long_tolerates_omitted_defaults(tmp_path, monkeypatch):
    """`record-split` omits `lead_in`/`seconds`; Typer then passes OptionInfo.

    Regression: `float(lead_in)` raised TypeError, which broke `ultron
    record-split` end to end while `record-long` itself worked.
    """
    _stub_audio(monkeypatch)

    out = rl.record_long_command(label="lb", device=None, out_dir=str(tmp_path / "takes"))

    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == 20 * SAMPLE_RATE  # --seconds default unwrapped


def test_record_long_omitted_lead_in_still_counts_down(tmp_path, monkeypatch, capsys):
    _stub_audio(monkeypatch)

    rl.record_long_command(seconds=1.0, label="lb", device=None, out_dir=str(tmp_path / "t"))

    printed = capsys.readouterr().out
    assert "Recording 'lb' in 3s" in printed  # default lead-in unwrapped, not OptionInfo


def test_opt_unwraps_only_typer_objects():
    assert rl._opt(2.5, 20.0) == 2.5
    assert rl._opt("dataset", "dataset/takes") == "dataset"
    assert rl._opt(typer.Option(5.0), 20.0) == 20.0
    assert rl._opt(typer.Option(None), "x") == "x"

