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
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import typer
from rich.console import Console

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16
DTYPE = "int16"

console = Console()


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
) -> None:
    """Record one continuous take of a single label."""
    device_index = _pick_device(device)
    takes_dir = Path(out_dir)
    takes_dir.mkdir(parents=True, exist_ok=True)

    nn = _next_take_nn(takes_dir, label)
    out_path = takes_dir / f"take_{nn:02d}_{label}.wav"

    frames = int(SAMPLE_RATE * seconds)
    console.print(f"Recording {label} for {seconds}s -> {out_path.name} ...")
    recording = sd.rec(
        frames=frames,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        device=device_index,
    )
    sd.wait()
    _write_wav(out_path, recording)
    console.print(f"[green]DONE[/green] wrote {out_path} ({recording.shape[0]} samples)")
    console.print("Next: [bold]ultron split <take.wav> --label <label>[/bold]")


__all__ = ["record_long_command"]
