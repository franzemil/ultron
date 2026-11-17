"""``ultron split`` — split a long take into per-utterance WAVs (pyannote VAD).

Runs pyannote.audio Voice Activity Detection on a take WAV produced by
``ultron record-long``, slices each speech segment into its own WAV, and writes
``{label}_local_<nn>.wav`` under ``dataset/originals/<label>/``.

One label per take — you pass ``--label``, so every segment gets that label.
No STT, no diarization, nothing runs on XIAO / Pi.

Requires a Hugging Face token (``PYANNOTE_HF_TOKEN`` env or ``--hf-token``) to
download ``pyannote/voice-activity-detection``.

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
from rich.console import Console

from ultron.dataset import REGISTRY_IDS

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16

console = Console()

PYANNOTE_MODEL = "pyannote/voice-activity-detection"


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


def _load_pipeline(hf_token: Optional[str]):
    """Load pyannote VAD pipeline. Fails fast with a clear message if unavailable."""
    token = hf_token or os.environ.get("PYANNOTE_HF_TOKEN")
    if not token:
        console.print("[red]Missing Hugging Face token.[/red]")
        console.print("Set PYANNOTE_HF_TOKEN or pass --hf-token.")
        console.print(f"Model: {PYANNOTE_MODEL} (accept license on HF first).")
        raise typer.Exit(code=2)
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:  # pragma: no cover - heavy dep
        console.print("[red]pyannote.audio not installed.[/red]")
        console.print("pip install pyannote.audio")
        raise typer.Exit(code=2)
    try:
        pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL, use_auth_token=token)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to load pyannote VAD model: {exc}[/red]")
        console.print("Check your HF token and license acceptance at huggingface.co/pyannote/voice-activity-detection")
        raise typer.Exit(code=2)
    return pipeline


def split_command(
    take: str = typer.Argument(..., help="Path to take WAV (from ultron record-long)."),
    label: str = typer.Argument(..., help="Label slug (registry id, e.g. lb, wake, noise)."),
    hf_token: Optional[str] = typer.Option(
        None, "--hf-token", "-t", help="Hugging Face API token (or set PYANNOTE_HF_TOKEN)."
    ),
    out_dir: str = typer.Option(
        "dataset/originals", "--out-dir", "-o", help="Dataset output directory.",
    ),
) -> None:
    """Split a take into per-utterance WAVs labeled by --label."""
    registry = _registry()
    _validate_label(label, registry)

    take_path = Path(take)
    if not take_path.is_file():
        console.print(f"[red]Take not found: {take}[/red]")
        raise typer.Exit(code=2)

    samples = _read_wav(take_path)

    pipeline = _load_pipeline(hf_token)
    vad = pipeline({"waveform": samples[None, :].astype(np.float32) / 32768.0, "sample_rate": SAMPLE_RATE})

    segments = []
    for speech in vad.get_timeline().support():
        # pyannote Segment objects expose .start/.end; tuples also work.
        start = getattr(speech, "start", speech[0])
        end = getattr(speech, "end", speech[1])
        segments.append((float(start), float(end)))

    if not segments:
        console.print(f"[yellow]No speech detected in {take_path.name}[/yellow]")
        console.print("Re-record or check microphone level.")
        raise typer.Exit(code=1)

    out_root = Path(out_dir)
    label_dir = out_root / "originals" / label
    label_dir.mkdir(parents=True, exist_ok=True)

    nn = _next_nn(label_dir, label, "local")
    total = 0
    for start, end in segments:
        i_start = int(start * SAMPLE_RATE)
        i_end = int(end * SAMPLE_RATE)
        seg = samples[i_start:i_end]
        if len(seg) < SAMPLE_RATE // 20:  # skip segments shorter than 50 ms
            continue
        out_path = label_dir / f"{label}_local_{nn:02d}.wav"
        _write_wav(out_path, seg)
        nn += 1
        total += 1

    console.print(f"[green]DONE[/green] split {take_path.name} into {total} segment(s) for '{label}'")
    console.print(f"Wrote to {label_dir}")
    console.print("Next: [bold]ultron dataset validate[/bold]")


__all__ = ["split_command"]
