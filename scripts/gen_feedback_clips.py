#!/usr/bin/env python3
"""Generate ``devices/esp32/ultron_voice/clips.h`` from the committed feedback WAVs.

Task: spec 003-can-act **T14** ("audio feedback — Escuchando + comando"). FR-018 /
FR-037 / D-006 / Q-012: both clips are pre-rendered, committed under ``assets/`` and
played byte-identically on every target. The firmware must not read a file at build
time, and arduino-cli only sees the sketch folder, so the PCM is embedded as a
``static const int16_t`` array — the ``spike_audio/hola.h`` pattern, without the
``xxd`` dump's non-deterministic formatting or the 44-byte WAV header.

Deterministic and committed (same pattern as ``scripts/gen_esp32_config.py``): the
header is part of the repo so a clean checkout compiles, and ``--check`` re-renders
it and fails on drift (``tests/test_feedback_clips.py`` guards CI).

The WAV is the source of truth: this script validates 16 kHz / 16-bit / mono and the
NFR-010 duration budget (Escuchando ≤ 500 ms, comando ≤ 300 ms) and fails loudly
rather than embedding a clip the device cannot honour.

Usage:
    uv run python scripts/gen_feedback_clips.py            # write the header
    uv run python scripts/gen_feedback_clips.py --check    # fail on drift
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "devices/esp32/ultron_voice/clips.h"
DEFAULT_ASSETS = REPO_ROOT / "assets"

SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16
VALUES_PER_LINE = 12

# (asset stem, C identifier, NFR-010 budget in ms)
CLIPS = (
    ("escuchando", "ULTRON_CLIP_ESCUCHANDO", 500),
    ("comando", "ULTRON_CLIP_COMANDO", 300),
)


class ClipError(RuntimeError):
    """Loud, actionable failure — never embed a clip that breaks the contract."""


def read_wav(path: Path) -> tuple[bytes, list[int]]:
    """Return (raw file bytes, int16 samples) for a 16 kHz/16-bit/mono PCM WAV."""
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ClipError(f"clip not found: {path}") from exc
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ClipError(f"{path} is not a RIFF/WAVE file")

    fmt: tuple[int, ...] | None = None
    pcm: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + size + (size & 1)  # chunks are word-aligned

    if fmt is None or pcm is None:
        raise ClipError(f"{path}: missing fmt or data chunk")
    audio_format, channels, rate, _byte_rate, _align, bits = fmt
    if audio_format != 1:
        raise ClipError(f"{path}: expected PCM (format 1), got format {audio_format}")
    if (channels, rate, bits) != (CHANNELS, SAMPLE_RATE, BITS):
        raise ClipError(
            f"{path}: expected {SAMPLE_RATE} Hz / {BITS}-bit / mono, "
            f"got {rate} Hz / {bits}-bit / {channels}ch"
        )
    if len(pcm) % 2:
        raise ClipError(f"{path}: odd data chunk length {len(pcm)}")
    return data, list(struct.unpack("<%dh" % (len(pcm) // 2), pcm))


def render_clip(samples: list[int], identifier: str) -> str:
    lines = []
    for i in range(0, len(samples), VALUES_PER_LINE):
        row = ", ".join(str(v) for v in samples[i : i + VALUES_PER_LINE])
        lines.append(f"    {row},")
    return (
        f"static const int16_t {identifier}[{identifier}_SAMPLES] = {{\n"
        + "\n".join(lines)
        + "\n};\n"
    )


def generate(assets_dir: Path = DEFAULT_ASSETS) -> str:
    entries: list[tuple[str, str, list[int], str]] = []
    for stem, identifier, budget_ms in CLIPS:
        path = assets_dir / f"{stem}.wav"
        raw, samples = read_wav(path)
        duration_ms = len(samples) * 1000 // SAMPLE_RATE
        if duration_ms > budget_ms:
            raise ClipError(
                f"{path}: {duration_ms} ms exceeds the {budget_ms} ms budget (NFR-010) — "
                "re-render with a faster 'say' rate"
            )
        entries.append((stem, identifier, samples, hashlib.sha256(raw).hexdigest()))

    out = [
        "/*",
        " * clips.h — GENERATED FILE, DO NOT EDIT.",
        " *",
        " * Source of truth: assets/escuchando.wav + assets/comando.wav (spec 003 T14,",
        " * FR-018 / FR-037 / D-006 / Q-012). Rendered by scripts/render_feedback_assets.sh,",
        " * embedded here by scripts/gen_feedback_clips.py so the firmware streams the clips",
        " * straight out of flash (no file system, no runtime TTS).",
        " *",
        " * Regenerate after any asset change:",
        " *",
        " *     uv run python scripts/gen_feedback_clips.py",
        " *",
        " * CI guard: tests/test_feedback_clips.py re-renders this header and fails on drift.",
        " *",
        " * Format: 16 kHz / 16-bit / mono int16 PCM, WAV header stripped. The bytes are the",
        " * same on every target — the Orange Pi (spec 005) plays the WAV files unchanged.",
        " */",
        "",
        "#ifndef ULTRON_CLIPS_H",
        "#define ULTRON_CLIPS_H",
        "",
        "#include <stdint.h>",
        "",
        f"#define ULTRON_CLIP_SAMPLE_RATE {SAMPLE_RATE}u",
        "",
    ]
    for stem, identifier, samples, digest in entries:
        duration_ms = len(samples) * 1000 // SAMPLE_RATE
        out.append(f"/* {stem}.wav — {len(samples)} samples = {duration_ms} ms @ {SAMPLE_RATE} Hz */")
        out.append(f"#define {identifier}_SAMPLES {len(samples)}u")
        out.append(f"#define {identifier}_MS {duration_ms}u")
        out.append(f'#define {identifier}_SHA256 "{digest}"')
        out.append(render_clip(samples, identifier))
    out.append("#endif /* ULTRON_CLIPS_H */")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="header to write")
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed header is stale",
    )
    args = parser.parse_args(argv)

    try:
        text = generate(args.assets)
    except ClipError as exc:
        print(f"gen_feedback_clips: {exc}", file=sys.stderr)
        return 2

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.is_file() else ""
        if current != text:
            print(
                f"gen_feedback_clips: {args.out} is stale — run "
                "`uv run python scripts/gen_feedback_clips.py`",
                file=sys.stderr,
            )
            return 1
        print(f"gen_feedback_clips: {args.out} is up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"gen_feedback_clips: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
