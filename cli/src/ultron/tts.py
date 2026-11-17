"""TTS providers for synthetic phrase rendering (T5.1).

Two engines:

* ``say`` — macOS built-in (``say -v <voice> -r <rate>``), AIFF converted to
  16 kHz / 16-bit / mono WAV with ffmpeg. Fully offline, no network TTS.
* ``mock`` — deterministic synthesized tone; used in tests/CI and when ``say``
  is unavailable. Same seed => identical audio bytes.

Real network TTS (edge-tts/gTTS) is intentionally NOT a default engine: the
lampara-aliz lesson is that synthetic generation must work offline and be
reproducible from seed. If network TTS is unavailable on a machine, tests use
the mock engine and say-dependent cases are skipped.
"""

from __future__ import annotations

import abc
import hashlib
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit

DEFAULT_MOCK_VOICES = ("Mónica", "Paulina", "Eddy")
DEFAULT_MOCK_SPEEDS = ("0.9", "1.0", "1.1")


class TTSError(RuntimeError):
    pass


class TTSProvider(abc.ABC):
    @abc.abstractmethod
    def render(self, phrase: str, voice: str, speed: str, out: Path, dry_run: bool = False) -> None:
        """Render `phrase` with `voice` at `speed` into `out` (16k/16-bit/mono WAV)."""


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise TTSError("ffmpeg is required for TTS rendering (ffmpeg not found on PATH)")
    return path


def say_voices() -> list[str]:
    """Spanish voice names from macOS `say -v '?'`, deterministic order."""
    say = shutil.which("say")
    if not say:
        return []
    result = subprocess.run([say, "-v", "?"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    voices: list[str] = []
    for line in result.stdout.splitlines():
        # "Mónica              es_ES    # ¡Hola! Me llamo Mónica."
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("es_"):
            voices.append(parts[0])
    return voices


def _ffmpeg_convert(aiff: Path, out: Path, dry_run: bool = False) -> None:
    ffmpeg = require_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(aiff),
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",
        "-sample_fmt", "s16",
        "-acodec", "pcm_s16le",
        str(out),
    ]
    if dry_run:
        print("  " + " ".join(cmd))
        return
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TTSError(
            f"ffmpeg failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()[:500]}"
        )


class SayProvider(TTSProvider):
    BASE_WPM = 170

    def render(self, phrase: str, voice: str, speed: str, out: Path, dry_run: bool = False) -> None:
        say = shutil.which("say")
        if not say:
            raise TTSError("`say` not found on this machine (use --engine mock)")
        rate = max(40, min(650, int(self.BASE_WPM * float(speed))))
        out.parent.mkdir(parents=True, exist_ok=True)
        aiff = out.with_suffix(".aiff")
        if dry_run:
            print(f'  say -v {voice} -r {rate} -o {aiff} {phrase!r}')
            _ffmpeg_convert(aiff, out, dry_run=True)
            return
        try:
            result = subprocess.run(
                [say, "-v", voice, "-r", str(rate), "-o", str(aiff), phrase],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise TTSError(f"`say` failed ({result.returncode}): {result.stderr.strip()[:500]}")
            _ffmpeg_convert(aiff, out, dry_run=False)
        finally:
            aiff.unlink(missing_ok=True)


class MockProvider(TTSProvider):
    """Deterministic offline stand-in: a sine tone whose parameters derive from
    (phrase, voice, speed, seed). Fully deterministic — no audio hardware, no network."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def render(self, phrase: str, voice: str, speed: str, out: Path, dry_run: bool = False) -> None:
        import random
        rng = random.Random(self.seed)
        if dry_run:
            print(f'  mock-tts phrase={phrase!r} voice={voice} speed={speed} -> {out}')
            return
        digest = hashlib.sha256(f"{phrase}|{voice}|{speed}|{self.seed}".encode()).digest()
        base_freq = 120 + (digest[0] % 320)           # 120..439 Hz
        jitter = rng.uniform(-6.0, 6.0)               # seed matters here
        freq = base_freq + jitter
        duration = max(0.7, min(2.0, 1.0 / float(speed)))
        amplitude = 0.10
        n = int(SAMPLE_RATE * duration)
        frames = b"".join(
            struct.pack(
                "<h",
                int(amplitude * 32767 * math.sin(2 * math.pi * freq * (i / SAMPLE_RATE))),
            )
            for i in range(n)
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(SAMPLE_WIDTH)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(frames)


def provider_for(engine: str, seed: int = 42) -> TTSProvider:
    engine = engine.lower()
    if engine == "say":
        return SayProvider()
    if engine == "mock":
        return MockProvider(seed=seed)
    if engine == "auto":
        return SayProvider() if shutil.which("say") else MockProvider(seed=seed)
    raise TTSError(f"unknown TTS engine: {engine} (use say|mock|auto)")
