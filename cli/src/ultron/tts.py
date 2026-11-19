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
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit

DEFAULT_MOCK_VOICES = ("Mónica", "Paulina", "Eddy")
DEFAULT_MOCK_SPEEDS = ("0.9", "1.0", "1.1")

# Kokoro-82M (Apache-2.0) Spanish voices; lang_code "e" in KPipeline.
DEFAULT_KOKORO_VOICES = ("ef_dora", "em_alex", "em_santa")
KOKORO_LANG_CODE = "e"  # Spanish
KOKORO_SAMPLE_RATE = 24000  # Kokoro renders at 24 kHz; ffmpeg brings it to 16 kHz


class TTSError(RuntimeError):
    pass


class TTSProvider(abc.ABC):
    @abc.abstractmethod
    def render(self, phrase: str, voice: str, speed: str, out: Path, dry_run: bool = False) -> None:
        """Render `phrase` with `voice` at `speed` into `out` (16k/16-bit/mono WAV)."""


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Fall back to the ffmpeg binary vendored by imageio-ffmpeg (installable
    # via uv on Windows, where ffmpeg is not on PATH by default).
    try:
        import imageio_ffmpeg  # type: ignore

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            return path
    except ImportError:
        pass
    raise TTSError(
        "ffmpeg is required for TTS rendering (ffmpeg not found on PATH). "
        "Install it (e.g. `winget install ffmpeg`, `choco install ffmpeg`, "
        "or `conda install -c conda-forge ffmpeg`) or run `uv sync` to pull "
        "the vendored binary via imageio-ffmpeg."
    )


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


class KokoroProvider(TTSProvider):
    """Open-source neural Spanish TTS: Kokoro-82M (Apache-2.0), fully offline.

    Kokoro renders float32 at 24 kHz; ffmpeg resamples to the pipeline's
    16 kHz / 16-bit / mono and the same seeded determinism rule applies
    (same phrase + voice + speed => same bytes for a pinned model).

    The 82 MB model is fetched once from Hugging Face and cached by the
    ``kokoro`` package; after that the machine can render offline. Install with
    ``uv sync --extra synth``.
    """

    _pipeline = None  # class-level cache: loading the model takes ~60 s

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed

    def _get_pipeline(self):
        if KokoroProvider._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:  # pragma: no cover - depends on env
                raise TTSError(
                    "kokoro is not installed — run `uv sync --extra synth` "
                    "(or `uv pip install kokoro`), then retry with --engine kokoro"
                ) from exc
            KokoroProvider._pipeline = KPipeline(lang_code=KOKORO_LANG_CODE)
        return KokoroProvider._pipeline

    def render(self, phrase: str, voice: str, speed: str, out: Path, dry_run: bool = False) -> None:
        if dry_run:
            print(f'  kokoro -v {voice} -s {speed} {phrase!r} -> {out}')
            return
        import numpy as np

        pipeline = self._get_pipeline()
        chunks = [audio for _gs, _ps, audio in pipeline(phrase, voice=voice, speed=float(speed))]
        if not chunks:
            raise TTSError(f"kokoro produced no audio for {phrase!r} (voice={voice})")
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_name(out.stem + ".24k.wav")
        try:
            _write_pcm16(raw, audio, KOKORO_SAMPLE_RATE)
            _ffmpeg_convert(raw, out)
        finally:
            raw.unlink(missing_ok=True)


def _write_pcm16(path: Path, audio, sample_rate: int) -> None:
    """Write float audio in [-1, 1] as 16-bit PCM mono (no extra dependency)."""
    import numpy as np

    clipped = np.clip(np.asarray(audio, dtype="float32"), -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes((clipped * 32767.0).astype("<i2").tobytes())


def provider_for(engine: str, seed: int = 42) -> TTSProvider:
    engine = engine.lower()
    if engine == "say":
        return SayProvider()
    if engine == "kokoro":
        return KokoroProvider(seed=seed)
    if engine == "mock":
        return MockProvider(seed=seed)
    if engine == "auto":
        return SayProvider() if shutil.which("say") else MockProvider(seed=seed)
    raise TTSError(f"unknown TTS engine: {engine} (use say|kokoro|mock|auto)")


def default_voices(engine: str) -> tuple[str, ...]:
    """Spanish voices that suit the requested engine (no probing for kokoro)."""
    engine = engine.lower()
    if engine == "kokoro":
        return DEFAULT_KOKORO_VOICES
    if engine == "mock":
        return DEFAULT_MOCK_VOICES
    return tuple(say_voices()) or DEFAULT_MOCK_VOICES
