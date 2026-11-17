"""``ultron split`` — split a long take into per-utterance WAVs (Silero VAD).

Runs Silero VAD on a take WAV produced by ``ultron record-long``, slices each
speech segment into its own WAV, and writes ``{label}_local_<nn>.wav`` under
``dataset/originals/<label>/``.

One label per take — you pass ``--label``, so every segment gets that label.
No STT, no HF token, no license acceptance needed.

Beyond T4/T8 scope (T9). Host-side only.
"""

from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import typer
from typer.models import OptionInfo
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from ultron.dataset import REGISTRY_IDS

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16

console = Console()


def _opt(value, default):
    """Return the plain value from a typer OptionInfo or the value itself.

    When Typer-decorated commands are called directly (not via the CLI),
    parameter defaults are raw OptionInfo objects; unwrap them.
    """
    if isinstance(value, OptionInfo):
        return value.default
    return value


def _registry() -> dict:
    """Load model/registry.json."""
    path = Path("model/registry.json")
    if not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _phrase_for(code: str, registry: dict) -> str:
    """Return the display phrase for a code (Spanish), falling back to the id."""
    entry = {}
    if code == "wake":
        entry = registry.get("wake", {})
    elif code == "noise":
        entry = registry.get("noise", {})
    else:
        for cmd in registry.get("commands", []):
            if cmd.get("id") == code:
                entry = cmd
                break
    phrases = entry.get("phrase") or [code]
    return phrases[0] if phrases else code


def _validate_label(label: str, registry: dict) -> None:
    """Reject labels not in registry.json (FR-030)."""
    valid = set(REGISTRY_IDS)
    if label not in valid:
        console.print(f"[red]Unknown label '{label}'.[/red]")
        console.print(f"Valid codes: {', '.join(sorted(valid))}")
        raise typer.Exit(code=2)


def _read_wav(path: Path) -> np.ndarray:
    """Read a 16 kHz / 16-bit / mono WAV into int16 numpy array."""
    import wave as _wave

    with _wave.open(str(path), "rb") as w:
        n = w.getnframes()
        frames = w.readframes(n)
    samples = np.frombuffer(frames, dtype="<i2")
    return samples


def _write_wav(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())


def _existing_count(out_root: Path, label: str) -> int:
    label_dir = out_root / "originals" / label
    if not label_dir.is_dir():
        return 0
    return len(
        [
            p
            for p in label_dir.iterdir()
            if p.name.startswith(f"{label}_local_") and p.suffix == ".wav"
        ]
    )


def _next_nn(label_dir: Path, label: str, mic: str = "local") -> int:
    prefix = f"{label}_{mic}_"
    best = 0
    if not label_dir.is_dir():
        return 1
    for name in os.listdir(label_dir):
        if not (name.startswith(prefix) and name.endswith(".wav")):
            continue
        try:
            nn = int(name[len(prefix) : -4])
        except ValueError:
            continue
        if nn > best:
            best = nn
    return best + 1


def _load_vad():
    """Load the Silero VAD model (small ONNX/torch model, no token needed)."""
    try:
        from silero_vad import load_silero_vad, get_speech_timestamps
    except ImportError:
        console.print("[red]silero-vad not installed.[/red]")
        console.print("pip install silero-vad")
        raise typer.Exit(code=2)
    try:
        model = load_silero_vad()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to load Silero VAD model: {exc}[/red]")
        raise typer.Exit(code=2)
    return model, get_speech_timestamps


def split_command(
    take: str = typer.Argument(..., help="Path to take WAV (from ultron record-long)."),
    label: str = typer.Argument(..., help="Label slug (registry id, e.g. lb, wake, noise)."),
    out_dir: str = typer.Option(
        "dataset/originals", "--out-dir", "-o", help="Dataset output directory.",
    ),
    min_speech_ms: int = typer.Option(
        250, "--min-speech-ms", help="Minimum speech segment length in milliseconds."
    ),
    min_silence_ms: int = typer.Option(
        300, "--min-silence-ms", help="Minimum silence between segments in milliseconds."
    ),
) -> None:
    """Split a take into per-utterance WAVs labeled by --label (Silero VAD)."""
    min_speech_ms = _opt(min_speech_ms, 250)
    min_silence_ms = _opt(min_silence_ms, 300)
    registry = _registry()
    _validate_label(label, registry)

    take_path = Path(take)
    if not take_path.is_file():
        console.print(f"[red]Take not found: {take}[/red]")
        raise typer.Exit(code=2)

    samples = _read_wav(take_path)

    model, get_speech_timestamps = _load_vad()

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Running Silero VAD...", total=None)
        speech_ts = get_speech_timestamps(
            samples,
            model,
            sampling_rate=int(SAMPLE_RATE),
            min_speech_duration_ms=int(min_speech_ms),
            min_silence_duration_ms=int(min_silence_ms),
        )
        progress.update(task, completed=1, total=1)

    segments = [(ts["start"], ts["end"]) for ts in speech_ts]

    if not segments:
        console.print(f"[yellow]No speech detected in {take_path.name}[/yellow]")
        console.print("Re-record or check microphone level.")
        raise typer.Exit(code=1)

    out_root = Path(out_dir)
    label_dir = out_root / "originals" / label
    label_dir.mkdir(parents=True, exist_ok=True)

    nn = _next_nn(label_dir, label, "local")
    total = 0
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task(f"Writing {len(segments)} segment(s)...", total=len(segments))
        for start, end in segments:
            seg = samples[start:end]
            if len(seg) < SAMPLE_RATE // 20:  # skip segments shorter than 50 ms
                progress.advance(task)
                continue
            out_path = label_dir / f"{label}_local_{nn:02d}.wav"
            _write_wav(out_path, seg)
            nn += 1
            total += 1
            progress.advance(task)

    console.print(f"[green]DONE[/green] split {take_path.name} into {total} WAV example(s) for '{label}'")
    console.print(f"Wrote to {label_dir}")
    console.print("Next: [bold]ultron dataset validate[/bold]")


__all__ = ["split_command"]
