"""``ultron record`` — interactively record training WAVs from the host mic.

Prompts the owner to select which command codes to record using a
Rich-based checkbox interface, then records ``--seconds`` of 16 kHz /
16-bit / mono audio per attempt until ``--samples`` examples exist per
selected code. Writes ``{label}_local_{nn:02d}.wav`` under
``dataset/originals/<label>/`` (mic tag ``local``), ready for
``ultron dataset ingest`` / ``validate``.

This is the host-side companion to the device recorders
(devices/local/record.py, devices/orangepi/record.py,
devices/esp32/wav_recorder/wav_recorder.ino) — same WAV contract, mic tag
``local``.
"""

from __future__ import annotations

import os
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
import sounddevice as sd
import typer

from ultron.dataset import REGISTRY_IDS

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16
DTYPE = "int16"

console = Console()


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
    return len([p for p in label_dir.iterdir() if p.name.startswith(f"{label}_local_") and p.suffix == ".wav"])


def _render_checkbox(codes: list[str], selected: set[str], out_root: Path, samples: int) -> str:
    """Render the checkbox list as a Rich panel."""
    lines = []
    for i, code in enumerate(codes, start=1):
        marked = "[x]" if code in selected else "[ ]"
        have = _existing_count(out_root, code)
        need = max(0, samples - have)
        lines.append(f"  {i:2d}. {marked} {code:<12} (need {need})")
    body = "\n".join(lines)
    hint = "[dim]Enter comma-separated numbers to toggle (e.g. 2,3,11), 'a' = all, 'n' = none, Enter = confirm[/dim]"
    return Panel(body, title="Select codes to record", subtitle=hint, border_style="cyan")


def _select_codes(codes: list[str], out_root: Path, samples: int) -> set[str]:
    """Interactive checkbox selection of registry codes."""
    selected: set[str] = set()
    while True:
        try:
            console.clear()
        except Exception:
            pass
        console.print(_render_checkbox(codes, selected, out_root, samples))
        ans = Prompt.ask("> ").strip().lower()
        if ans == "":
            break
        if ans == "a":
            selected = set(codes)
            continue
        if ans == "n":
            selected = set()
            continue
        toggled = False
        for token in ans.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                idx = int(token) - 1
            except ValueError:
                console.print(f"[yellow]skip invalid token: {token}[/yellow]")
                continue
            if 0 <= idx < len(codes):
                code = codes[idx]
                if code in selected:
                    selected.discard(code)
                else:
                    selected.add(code)
                toggled = True
            else:
                console.print(f"[yellow]out of range: {token}[/yellow]")
        if not toggled and ans not in ("a", "n"):
            console.print("[dim]Press Enter to confirm selection.[/dim]")
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

    codes = sorted(REGISTRY_IDS)
    console.print("Recordable codes: " + ", ".join(codes))

    selected = _select_codes(codes, out_root, samples)

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
