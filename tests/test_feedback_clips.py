"""Feedback clip assets ↔ firmware header — T14 (spec 003-can-act).

`devices/esp32/ultron_voice/clips.h` embeds the two committed confirmation clips
(`assets/escuchando.wav`, `assets/comando.wav`) so an `arduino-cli compile` on a
clean checkout needs neither `say` nor `ffmpeg`. That only stays true if the
header is regenerated whenever an asset changes — the same drift guard
`scripts/gen_esp32_config.py --check` applies to `config.h`.

These tests never re-render audio (no `say`, no network): they re-encode the
already-committed WAVs and compare bytes with the checked-in header.
"""

from __future__ import annotations

import hashlib
import importlib.util
import struct
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HEADER = REPO_ROOT / "devices/esp32/ultron_voice/clips.h"
ASSETS = REPO_ROOT / "assets"
GENERATOR = REPO_ROOT / "scripts/gen_feedback_clips.py"
SKETCH = REPO_ROOT / "devices/esp32/ultron_voice/ultron_voice.ino"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_feedback_clips", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


# ---- asset contract (FR-018 / FR-037 / NFR-010) ---------------------------


@pytest.mark.parametrize("stem,budget_ms", [("escuchando", 500), ("comando", 300)])
def test_committed_assets_match_the_format_and_budget(stem: str, budget_ms: int) -> None:
    path = ASSETS / f"{stem}.wav"
    _raw, samples = gen.read_wav(path)
    duration_ms = len(samples) * 1000 // gen.SAMPLE_RATE
    assert duration_ms <= budget_ms, f"{path.name} is {duration_ms} ms > {budget_ms} ms"
    assert len(samples) > 0


def test_assets_are_true_wav_not_raw_pcm() -> None:
    """The Pi (spec 005) plays these files directly — they must stay valid WAVs."""
    for stem, _identifier, _budget in gen.CLIPS:
        path = ASSETS / f"{stem}.wav"
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == gen.SAMPLE_RATE
            assert wav.getnchannels() == gen.CHANNELS
            assert wav.getsampwidth() == gen.BITS // 8
            assert wav.getnframes() > 0
        assert path.read_bytes()[:4] == b"RIFF"


def test_header_embeds_the_committed_bytes() -> None:
    """Every embedded PCM sample and the provenance sha256 come from the WAV."""
    header = HEADER.read_text(encoding="utf-8")
    for stem, identifier, _budget in gen.CLIPS:
        raw = (ASSETS / f"{stem}.wav").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert f'#define {identifier}_SHA256 "{digest}"' in header, f"{stem}: sha256 drift"

        _raw, samples = gen.read_wav(ASSETS / f"{stem}.wav")
        assert f"#define {identifier}_SAMPLES {len(samples)}u" in header
        expected = ", ".join(str(v) for v in samples[:12])
        assert expected in header, f"{stem}: PCM drift"


def test_committed_header_is_not_stale() -> None:
    committed = HEADER.read_text(encoding="utf-8")
    assert gen.generate(ASSETS) == committed, (
        "clips.h is stale — run `uv run python scripts/gen_feedback_clips.py`"
    )


# ---- generator guards -----------------------------------------------------


def _write_wav(path: Path, samples: list[int], rate: int = 16000, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return path


def test_generator_rejects_a_clip_over_budget(tmp_path: Path) -> None:
    # 600 ms of tone for the 500 ms "Escuchando" slot.
    too_long = [1000] * int(0.6 * gen.SAMPLE_RATE)
    _write_wav(tmp_path / "escuchando.wav", too_long)
    _write_wav(tmp_path / "comando.wav", [1000] * 4096)
    with pytest.raises(gen.ClipError, match="budget"):
        gen.generate(tmp_path)


def test_generator_rejects_a_wrong_format(tmp_path: Path) -> None:
    _write_wav(tmp_path / "escuchando.wav", [0] * 1000, rate=8000)
    _write_wav(tmp_path / "comando.wav", [0] * 1000)
    with pytest.raises(gen.ClipError, match="16000"):
        gen.generate(tmp_path)


# ---- sketch wiring (the library owns the interval arithmetic) -------------


def test_sketch_arms_the_library_from_the_asset_durations() -> None:
    """Options.wakeClipMs/commandClipMs come from clips.h, never a literal."""
    sketch = SKETCH.read_text(encoding="utf-8")
    assert (
        "options.wakeClipMs = ultron::feedback_clip(ultron::kFeedbackEscuchando)->duration_ms"
        in sketch
    )
    assert (
        "options.commandClipMs = ultron::feedback_clip(ultron::kFeedbackComando)->duration_ms"
        in sketch
    )
    for magic in ("wakeClipMs = 441", "wakeClipMs = 500", "commandClipMs = 256"):
        assert magic not in sketch


def test_sketch_does_not_arm_the_state_machine_itself() -> None:
    """One arming path only: two would double-count the interval (NFR-010)."""
    sketch = SKETCH.read_text(encoding="utf-8")
    assert "ultron_sm_playback_start" not in sketch


def test_sketch_plays_both_clips_on_their_edges() -> None:
    """FR-018 on the Wake hook, FR-037 on the Command hook."""
    sketch = SKETCH.read_text(encoding="utf-8")
    wake = sketch.split("void onWake(", 1)[1].split("}", 1)[0]
    command = sketch.split("void onCommand(", 1)[1].split("}", 1)[0]
    assert "kFeedbackEscuchando" in wake
    assert "kFeedbackComando" in command
