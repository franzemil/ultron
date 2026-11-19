"""``ultron record-long`` — record a continuous 20 s take of ONE label.

Records N seconds (default 20) of 16 kHz / 16-bit / mono audio from the host
microphone into a single take WAV under ``dataset/takes/take_<nn>_<label>.wav``.

The take contains only one label's utterances, so downstream splitting with
pyannote.audio VAD needs no STT — every segment gets this same label.

This is host-side only (nothing runs on XIAO / Pi). Beyond T4/T8 scope (T9).
"""

from __future__ import annotations

import os
import sys
import time
import wave
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
import sounddevice as sd
import typer
from rich.console import Console
from rich.live import Live
from typer.models import OptionInfo

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16
DTYPE = "int16"

TICK = 0.1  # countdown refresh interval (seconds)
WRAP_UP_S = 5.0  # last N seconds are flagged so the speaker knows to stop
BAR_WIDTH = 20

console = Console()


def _opt(value, default):
    """Return the plain value from a typer OptionInfo or the value itself.

    Commands are called directly (not only through the CLI), e.g. by
    ``record-split``, so an omitted parameter arrives as ``OptionInfo`` instead
    of its default. Unwrapping here keeps both call paths identical.
    """
    if isinstance(value, OptionInfo):
        return default
    return value


def countdown(duration: float, *, tick: float = TICK) -> Iterator[float]:
    """Yield the seconds *remaining* from ``duration`` down to one tick.

    Pure and side-effect free so the visible countdown can be tested without a
    clock: ``list(countdown(0.3, tick=0.1)) == [0.3, 0.2, 0.1]``.
    """
    if duration <= 0:
        return
    steps = max(int(round(duration / tick)), 1)
    for i in range(steps, 0, -1):
        yield round(i * tick, 6)


def progress_line(
    label: str, elapsed: float, total: float, *, width: int = BAR_WIDTH
) -> str:
    """The single line shown while recording: bar, elapsed/total, remaining.

    The remaining seconds are the point: the owner needs to know *when to stop
    talking*, not how long they have been talking.
    """
    total = max(float(total), 0.0)
    ratio = 0.0 if total <= 0 else min(max(elapsed / total, 0.0), 1.0)
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    left = max(total - elapsed, 0.0)
    line = f"● recording {label}  {bar}  {elapsed:4.1f}/{total:.1f}s  left {left:4.1f}s"
    if left <= WRAP_UP_S:
        line += "  ← wrap up"
    return line


def lead_in_messages(label: str, seconds: float) -> list[str]:
    """Lines printed before capture starts (3, 2, 1 …) so the speaker is ready."""
    return [
        f"Recording '{label}' in {int(round(remaining))}s — get ready…"
        for remaining in countdown(seconds, tick=1.0)
    ]


def _run_recording_progress(
    label: str,
    seconds: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
    tick: float = TICK,
) -> None:
    """Show the live countdown for ``seconds`` while the take is being captured."""
    with Live(console=console, refresh_per_second=1 / tick, transient=True) as live:
        shown = 0
        for remaining in countdown(seconds, tick=tick):
            live.update(progress_line(label, seconds - remaining, seconds))
            shown += 1
            sleep(tick)
        if shown:  # final full-bar frame, so the bar does not stop at 19.9/20.0
            live.update(progress_line(label, seconds, seconds))



def _next_take_nn(takes_dir: Path, label: str) -> int:
    """Return the next free two-digit sequence for take_<nn>_<label>.wav."""
    prefix = f"take_"
    suffix = f"_{label}.wav"
    best = 0
    if not takes_dir.is_dir():
        return 1
    for name in os.listdir(takes_dir):
        if not (name.startswith(prefix) and name.endswith(suffix)):
            continue
        middle = name[len(prefix) : -len(suffix)]
        try:
            nn = int(middle)
        except ValueError:
            continue
        if nn > best:
            best = nn
    return best + 1


def _list_capture_devices() -> list:
    return [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]


def _pick_device(device: Optional[str]) -> int:
    """Resolve capture device index; fail fast if none is available."""
    inputs = _list_capture_devices()
    if not inputs:
        console.print("[red]No capture device found.[/red]")
        console.print("Plug in a microphone and rerun.")
        sys.exit(2)
    if device is None:
        try:
            default = sd.default.device[0]
            if default is not None:
                return int(default)
        except Exception:
            pass
        return int(inputs[0]["index"])
    try:
        return int(device)
    except ValueError:
        for d in inputs:
            if device.lower() in str(d.get("name", "")).lower():
                return int(d["index"])
        console.print(f"[red]Capture device '{device}' not found.[/red]")
        console.print("Detected capture devices:")
        for d in inputs:
            console.print(f"  [{d['index']}] {d['name']}")
        sys.exit(2)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())


def record_long_command(
    seconds: float = typer.Option(20.0, "--seconds", "-s", help="Seconds to record (default 20)."),
    label: str = typer.Argument(..., help="Label slug (registry id, e.g. lb, wake, noise)."),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    out_dir: str = typer.Option(
        "dataset/takes", "--out-dir", "-o", help="Takes output directory (default dataset/takes).",
    ),
    lead_in: float = typer.Option(
        3.0, "--lead-in", help="Countdown before capture starts in seconds (0 disables)."
    ),
) -> None:
    """Record one continuous take of a single label, with a visible countdown."""
    seconds = float(_opt(seconds, 20.0))
    lead_in = float(_opt(lead_in, 3.0))
    out_dir = _opt(out_dir, "dataset/takes")
    device = _opt(device, None)
    device_index = _pick_device(device)
    takes_dir = Path(out_dir)
    takes_dir.mkdir(parents=True, exist_ok=True)

    nn = _next_take_nn(takes_dir, label)
    out_path = takes_dir / f"take_{nn:02d}_{label}.wav"

    frames = int(SAMPLE_RATE * seconds)
    console.print(
        f"Recording {label} for {seconds:g}s -> {out_path.name} "
        f"(stop speaking when the countdown hits 0)"
    )
    for message in lead_in_messages(label, lead_in):
        console.print(f"[yellow]{message}[/yellow]")
        time.sleep(1.0)

    recording = sd.rec(
        frames=frames,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        device=device_index,
    )
    _run_recording_progress(label, seconds)
    sd.wait()
    _write_wav(out_path, recording)
    duration = recording.shape[0] / SAMPLE_RATE
    console.print(f"[green]DONE[/green] wrote {out_path.name} ({duration:.1f}s take)")
    return out_path


__all__ = ["record_long_command"]
