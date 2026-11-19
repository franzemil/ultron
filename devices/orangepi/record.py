#!/usr/bin/env python3
"""Orange Pi Zero 2W WAV recorder.

Records 16 kHz / 16-bit / mono WAV clips from the owner-supplied USB
microphone, labeled by phrase, for ultron training data (US-002 / T4).

Usage:
    python3 record.py <label> [--seconds 5] [--device DEVICE]

Writes <label>_pi_<nn>.wav to the current directory (mirrors the
dataset/ originals layout: {label}_{mic}_{nn}.wav with mic=pi).
"""

from __future__ import annotations

import argparse
import sys
import wave
from datetime import datetime
from pathlib import Path

try:
    import numpy as np
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover - hardware only
    print(f"record.py: missing dependency: {exc}", file=sys.stderr)
    print("Install with: pip install sounddevice numpy", file=sys.stderr)
    sys.exit(2)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit int16
DTYPE = "int16"


def list_capture_devices() -> list[dict]:
    """Return sounddevice input-capable devices."""
    return [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]


def pick_device(device: str | int | None) -> int:
    """Resolve the capture device index, failing fast if none is available."""
    inputs = list_capture_devices()
    if not inputs:
        print("record.py: no capture device found.", file=sys.stderr)
        print("Plug in an owner-supplied USB microphone and rerun.", file=sys.stderr)
        sys.exit(2)
    if device is None:
        return int(inputs[0]["index"])
    try:
        return int(device)
    except ValueError:
        # Match by name substring.
        for d in inputs:
            if device.lower() in str(d.get("name", "")).lower():
                return int(d["index"])
        print(f"record.py: capture device '{device}' not found.", file=sys.stderr)
        print("Detected capture devices:", file=sys.stderr)
        for d in inputs:
            print(f"  [{d['index']}] {d['name']}", file=sys.stderr)
        sys.exit(2)


def next_sequence(label: str, out_dir: Path) -> int:
    """Return the next free <nn> for <label>_pi_<nn>.wav in out_dir."""
    existing = sorted(out_dir.glob(f"{label}_pi_*.wav"))
    if not existing:
        return 1
    nums = []
    for p in existing:
        stem = p.stem  # label_pi_nn
        parts = stem.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            nums.append(int(parts[1]))
    return (max(nums) if nums else 0) + 1


def write_wav(path: Path, samples: np.ndarray) -> None:
    """Write int16 mono samples as a valid 16 kHz WAV file."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="record.py",
        description="Record a 16 kHz/16-bit/mono WAV clip on the Orange Pi.",
    )
    parser.add_argument("label", help="Phrase label slug (e.g. guiñador_derecho).")
    parser.add_argument(
        "--seconds",
        type=float,
        default=5.0,
        help="Recording duration in seconds (default: 5).",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="sounddevice input device index or name substring.",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Output directory (default: current directory).",
    )
    args = parser.parse_args(argv)

    device_index = pick_device(args.device)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    nn = next_sequence(args.label, out_dir)
    out_path = out_dir / f"{args.label}_pi_{nn:02d}.wav"

    frames = int(SAMPLE_RATE * args.seconds)
    print(
        f"record.py: label={args.label} device={device_index} "
        f"{args.seconds}s @ {SAMPLE_RATE} Hz/{SAMPLE_WIDTH * 8}-bit/mono -> {out_path}",
        file=sys.stderr,
    )

    recording = sd.rec(
        frames=frames,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=DTYPE,
        device=device_index,
    )
    sd.wait()

    write_wav(out_path, recording)
    print(f"record.py: DONE wrote {out_path} ({recording.shape[0]} samples)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
