"""``ultron record`` — interactively record training WAVs from the host mic.

Prompts the owner to select which command codes to record using a Rich/questionary
checkbox interface (space to toggle, Enter to confirm). Lists every registry code
with its Spanish phrase and the number of examples already on disk, then records
``--seconds`` of 16 kHz / 16-bit / mono audio per attempt until ``--samples``
examples exist per selected code. Writes ``{label}_local_{nn:02d}.wav`` under
``dataset/originals/<label>/`` (mic tag ``local``), ready for
``ultron dataset ingest`` / ``validate``.

This is the host-side companion to the device recorders
(devices/local/record.py, devices/orangepi/record.py,
devices/esp32/wav_recorder/wav_recorder.ino) — same WAV contract, mic tag
``local``.
"""

from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import questionary
import sounddevice as sd
import typer
from rich.console import Console

from ultron.dataset import REGISTRY_IDS

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16
DTYPE = "int16"

console = Console()


def _registry() -> dict:
    """Load model/registry.json (id -> {phrase, sigla, ...})."""
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


def _next_nn(label_dir: Path, label: str, mic: str) -> int:
    """Return the next free two-digit sequence for {label}_{mic}_{nn}.wav."""
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


def _record_once(label: str, seconds: float, device_index: int, out_path: Path) -> None:
    frames = int(SAMPLE_RATE * seconds)
    console.print(f"Recording {label} ({seconds}s) -> {out_path.name} ...")
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


def _select_codes(codes: list[str], out_root: Path, samples: int, registry: dict) -> list[str]:
    """Interactive checkbox selection (space toggles, Enter confirms).

    Each choice shows the code, its Spanish phrase, and current samples / target.
    """
    choices = []
    for code in codes:
        have = _existing_count(out_root, code)
        phrase = _phrase_for(code, registry)
        title = f"{code:<10} {phrase:<30} [{have}/{samples}]"
        choices.append(
            questionary.Choice(title=title, value=code, checked=(have < samples))
        )
    selected = questionary.checkbox(
        "Select codes to record (SPACE to toggle, ENTER to confirm):",
        choices=choices,
    ).ask()
    if selected is None:
        # User aborted (Ctrl-C)
        console.print("Aborted.")
        sys.exit(0)
    return selected


def record_command(
    seconds: float = typer.Option(5.0, "--seconds", "-s", help="Seconds per recording attempt."),
    samples: int = typer.Option(20, "--samples", "-n", help="Target number of examples per code."),
    device: Optional[str] = typer.Option(
        None, "--device", "-d", help="Input device index or name substring."
    ),
    out_dir: str = typer.Option(
        "dataset/originals", "--out-dir", "-o", help="Output dataset directory."
    ),
) -> None:
    """Interactively record training WAVs for selected registry codes."""
    device_index = _pick_device(device)
    out_root = Path(out_dir)

    registry = _registry()
    codes = sorted(REGISTRY_IDS)
    console.print("Recordable codes: " + ", ".join(codes))

    selected = _select_codes(codes, out_root, samples, registry)

    if not selected:
        console.print("No codes selected. Nothing to record.")
        sys.exit(0)

    total = 0
    for label in sorted(selected):
        label_dir = out_root / "originals" / label
        have = _existing_count(out_root, label)
        need = max(0, samples - have)
        console.print(f"\n[bold]{label}: have {have}, need {need}[/bold]")
        label_dir.mkdir(parents=True, exist_ok=True)
        nn = _next_nn(label_dir, label, "local")
        for _ in range(need):
            out_path = label_dir / f"{label}_local_{nn:02d}.wav"
            _record_once(label, seconds, device_index, out_path)
            nn += 1
            total += 1

    console.print(f"\n[green]Recorded {total} file(s).[/green]")
    console.print("Run: [bold]ultron dataset validate[/bold]")


__all__ = ["record_command"]
