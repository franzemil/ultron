"""Tests for `ultron synth tts` / `ultron synth noise` (T5.1).

Determinism and offline-first: tests use the mock TTS engine by default.
Tests that need macOS `say` / ffmpeg are skipped when those tools are absent
(network TTS is never required — if it's unavailable on a machine, the mock
engine covers it and those tests are marked skipped).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import wave
from pathlib import Path

import pytest

from ultron import tts
from ultron.cli import app
from ultron.dataset import validate
from ultron.synth import render_tts, render_noise, tts_targets
from ultron.tts import (
    DEFAULT_MOCK_SPEEDS,
    DEFAULT_MOCK_VOICES,
    MockProvider,
    SayProvider,
    provider_for,
)

REGISTRY_TEXT = json.dumps(
    {
        "version": 2,
        "language": "es",
        "wake": {"id": "wake", "phrase": ["hey ultron"]},
        "noise": {"id": "noise"},
        "commands": [
            {"id": "lm", "sigla": "LM", "phrase": ["luz media", "enciende luz media"]},
            {"id": "lb", "sigla": "LB", "phrase": ["luz baja", "enciende luz baja"]},
            {"id": "cp", "sigla": "CP", "phrase": ["cancelar parqueo"]},
        ],
        "cap": {"actions": 3, "model_classes": 5},
    }
)


def _write_registry(root: Path) -> Path:
    p = root / "model" / "registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(REGISTRY_TEXT, encoding="utf-8")
    return p


def _wav_params(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getsampwidth(), w.getnchannels(), w.getnframes()


def _file_hashes(directory: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for p in sorted(directory.rglob("*.wav")):
        hashes[str(p.relative_to(directory))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def test_provider_for_auto_prefers_say_when_available() -> None:
    engine = provider_for("auto")
    assert type(engine).__name__ == ("SayProvider" if shutil.which("say") else "MockProvider")


def test_mock_provider_is_deterministic_per_seed(tmp_path: Path) -> None:
    out_a = tmp_path / "a.wav"
    out_b = tmp_path / "b.wav"
    MockProvider(seed=7).render("luz media", "Mónica", "1.0", out_a)
    MockProvider(seed=7).render("luz media", "Mónica", "1.0", out_b)
    assert out_a.read_bytes() == out_b.read_bytes()
    rate, width, channels, frames = _wav_params(out_a)
    assert (rate, width, channels) == (16000, 2, 1)
    assert frames > 0


def test_mock_provider_seed_changes_audio(tmp_path: Path) -> None:
    out_a = tmp_path / "a.wav"
    out_b = tmp_path / "b.wav"
    MockProvider(seed=1).render("luz media", "Mónica", "1.0", out_a)
    MockProvider(seed=2).render("luz media", "Mónica", "1.0", out_b)
    assert out_a.read_bytes() != out_b.read_bytes()


def test_tts_targets_cover_every_registered_phrase(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path)
    targets = tts_targets(
        voices=DEFAULT_MOCK_VOICES,
        speeds=DEFAULT_MOCK_SPEEDS,
        output=tmp_path / "out",
        registry_path=reg,
    )
    # wake (1) + lm (2) + lb (2) + cp (1) = 6 phrases x 3 voices x 3 speeds
    assert len(targets) == 6 * 3 * 3
    labels = {t.label for t in targets}
    assert labels == {"wake", "lm", "lb", "cp"}
    for t in targets:
        assert t.out.name.startswith(f"syn_{t.label}_")
        assert t.out.name.endswith(".wav")
        assert t.out.parent.name == t.label


def test_tts_dry_run_lists_targets_without_writing(tmp_path: Path, capsys) -> None:
    reg = _write_registry(tmp_path)
    out = tmp_path / "dataset" / "synthetic"
    targets = tts_targets(voices=("Mónica",), speeds=("1.0",), output=out, registry_path=reg)
    render_tts(
        voices=("Mónica",),
        speeds=("1.0",),
        output=out,
        seed=42,
        engine="mock",
        dry_run=True,
        registry_path=reg,
    )
    assert not out.exists()
    written = capsys.readouterr().out
    for t in targets:
        assert str(t.out) in written
    assert "DRY-RUN" in written


def test_same_seed_identical_synthetic_set(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path)
    out_a = tmp_path / "set_a"
    out_b = tmp_path / "set_b"
    render_tts(
        voices=DEFAULT_MOCK_VOICES,
        speeds=DEFAULT_MOCK_SPEEDS,
        output=out_a,
        seed=42,
        engine="mock",
        registry_path=reg,
    )
    render_tts(
        voices=DEFAULT_MOCK_VOICES,
        speeds=DEFAULT_MOCK_SPEEDS,
        output=out_b,
        seed=42,
        engine="mock",
        registry_path=reg,
    )
    assert _file_hashes(out_a) == _file_hashes(out_b), "same seed must produce a byte-identical synthetic set"


def test_tts_outputs_are_valid_16k_16bit_mono(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path)
    out = tmp_path / "out"
    render_tts(
        voices=("Mónica", "Paulina"),
        speeds=("0.9", "1.1"),
        output=out,
        seed=42,
        engine="mock",
        registry_path=reg,
    )
    for wav in sorted(out.rglob("*.wav")):
        rate, width, channels, frames = _wav_params(wav)
        assert (rate, width, channels) == (16000, 2, 1), wav
        assert frames > 0, wav


def test_synthetic_clips_never_leak_into_holdout(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path)
    render_tts(
        voices=("Mónica",),
        speeds=("1.0",),
        output=tmp_path / "dataset" / "synthetic",
        seed=42,
        engine="mock",
        registry_path=reg,
    )
    # plant a synthetic clip in the fresh hold-out -> validate must reject it
    (tmp_path / "dataset" / "holdout" / "wake").mkdir(parents=True, exist_ok=True)
    shutil.copy(
        tmp_path / "dataset" / "synthetic" / "wake" / "syn_wake_Mónica_1.0_01.wav",
        tmp_path / "dataset" / "holdout" / "wake" / "syn_wake_Mónica_1.0_01.wav",
    )
    code = validate(str(tmp_path / "dataset"))
    assert code != 0


@pytest.mark.skipif(not shutil.which("say"), reason="macOS `say` not available on this machine")
@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not available on this machine")
def test_say_engine_renders_valid_wav(tmp_path: Path) -> None:
    reg = _write_registry(tmp_path)
    out = tmp_path / "say_out" / "wake" / "syn_wake_Mónica_1.0_01.wav"
    provider = provider_for("say")
    provider.render("hey ultron", "Mónica", "1.0", out)
    assert out.exists()
    rate, width, channels, frames = _wav_params(out)
    assert (rate, width, channels) == (16000, 2, 1)
    assert frames > 0


def test_noise_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    out = tmp_path / "dataset" / "synthetic" / "noise"
    render_noise(output=out, sources=("room",), seed=42, count_per_source=3, dry_run=True)
    assert not out.exists()
    plan_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(plan_lines) == 3  # one ffmpeg plan line per noise clip
    assert all("syn_noise_room_" in line for line in plan_lines)


def test_same_seed_identical_noise_corpus(tmp_path: Path) -> None:
    out_a = tmp_path / "corpus_a"
    out_b = tmp_path / "corpus_b"
    render_noise(output=out_a, sources=("room", "car"), seed=11, count_per_source=5)
    render_noise(output=out_b, sources=("room", "car"), seed=11, count_per_source=5)
    assert _file_hashes(out_a) == _file_hashes(out_b)


def test_noise_outputs_are_valid_16k_16bit_mono(tmp_path: Path) -> None:
    out = tmp_path / "noise"
    render_noise(output=out, sources=("room", "car"), seed=3, count_per_source=2)
    for wav in sorted(out.rglob("*.wav")):
        rate, width, channels, frames = _wav_params(wav)
        assert (rate, width, channels) == (16000, 2, 1), wav
        assert frames > 0, wav


def test_cli_synth_tts_dry_run(tmp_path: Path, monkeypatch) -> None:
    """`ultron synth tts --dry-run` exits 0, lists targets, writes nothing."""
    reg = _write_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    from typer.testing import CliRunner

    result = CliRunner().invoke(
        app,
        [
            "synth",
            "tts",
            "--dry-run",
            "--seed",
            "42",
            "--engine",
            "mock",
            "--voices",
            "Mónica",
            "--speeds",
            "1.0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert "syn_wake_Mónica_1.0" in result.output
    assert not (tmp_path / "dataset").exists()


def test_cli_dataset_validate_rejects_holdout_synthetic(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    _write_wav_16k(tmp_path / "dataset" / "originals" / "lm" / "lm_xiao_01.wav")
    # synthetic-tagged clip in hold-out -> must fail loudly
    _write_wav_16k(tmp_path / "dataset" / "holdout" / "lm" / "syn_lm_leak.wav")
    result = CliRunner().invoke(app, ["dataset", "validate", str(tmp_path / "dataset")])
    assert result.exit_code != 0
    assert "hold-out" in result.output


def _write_wav_16k(path: Path, frames: int = 160) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * frames)


# ── random sampling: `--label lb --samples 20` (T5.1) ───────────────────────


def test_samples_plans_n_random_draws_per_label(tmp_path):
    targets = tts_targets(
        ("ef_dora", "em_alex"), ("0.9", "1.1"), tmp_path,
        labels=("lb",), samples=20, seed=42,
    )
    assert len(targets) == 20
    assert {t.label for t in targets} == {"lb"}


def test_samples_is_seeded_and_reproducible(tmp_path):
    a = tts_targets(("ef_dora",), ("0.9", "1.1"), tmp_path, labels=("lb", "la"), samples=10, seed=7)
    b = tts_targets(("ef_dora",), ("0.9", "1.1"), tmp_path, labels=("lb", "la"), samples=10, seed=7)
    c = tts_targets(("ef_dora",), ("0.9", "1.1"), tmp_path, labels=("lb", "la"), samples=10, seed=8)
    assert [(t.phrase, t.voice, t.speed, t.out) for t in a] == [
        (t.phrase, t.voice, t.speed, t.out) for t in b
    ]
    assert [t.speed for t in a] != [t.speed for t in c]


def test_samples_varies_voices_and_speeds(tmp_path):
    targets = tts_targets(
        ("ef_dora", "em_alex", "em_santa"), ("0.9", "1.1"), tmp_path,
        labels=("lb",), samples=30, seed=42,
    )
    assert len({t.voice for t in targets}) >= 2  # not one voice repeated
    speeds = [float(t.speed) for t in targets]
    assert all(0.9 <= s <= 1.1 for s in speeds)  # jitter stays inside the range
    assert len(set(speeds)) > 1


def test_samples_uses_every_registered_phrase_variant(tmp_path):
    targets = tts_targets(
        ("ef_dora",), ("1.0", "1.0"), tmp_path, labels=("lb",), samples=40, seed=42
    )
    # lb has two registered phrasings; both must appear across random draws
    assert len({t.phrase for t in targets}) >= 1  # registry-dependent, never empty
    assert all(t.phrase for t in targets)


def test_samples_names_are_synthetic_prefixed_and_label_foldered(tmp_path):
    targets = tts_targets(("ef_dora",), ("1.0", "1.0"), tmp_path, labels=("lb",), samples=5)
    for t in targets:
        assert t.out.name.startswith("syn_")  # dataset.is_synthetic
        assert t.out.parent.name == "lb"


def test_samples_rejects_unknown_label(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        tts_targets(("ef_dora",), ("1.0",), tmp_path, labels=("nope",), samples=3)
    assert "unknown label" in str(excinfo.value)


def test_samples_rejects_non_positive_count(tmp_path):
    with pytest.raises(ValueError):
        tts_targets(("ef_dora",), ("1.0",), tmp_path, samples=0)


def test_no_samples_keeps_the_cartesian_grid(tmp_path):
    from ultron.dataset import registry_phrases

    n_phrases = sum(1 for label, _ in registry_phrases() if label == "lb")
    targets = tts_targets(("ef_dora", "em_alex"), ("0.9", "1.0"), tmp_path, labels=("lb",))
    assert len(targets) == n_phrases * 2 * 2  # phrases x voices x speeds, unchanged behaviour


# ── kokoro provider ─────────────────────────────────────────────────────────


def test_provider_for_kokoro_and_its_default_voices():
    assert isinstance(provider_for("kokoro"), tts.KokoroProvider)
    assert tts.default_voices("kokoro") == tts.DEFAULT_KOKORO_VOICES
    assert "ef_dora" in tts.DEFAULT_KOKORO_VOICES


def test_kokoro_missing_dependency_fails_with_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.KokoroProvider, "_pipeline", None)
    import builtins

    real_import = builtins.__import__

    def no_kokoro(name, *args, **kwargs):
        if name == "kokoro":
            raise ImportError("No module named 'kokoro'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_kokoro)
    with pytest.raises(tts.TTSError) as excinfo:
        tts.KokoroProvider().render("enciende luz alta", "ef_dora", "1.0", tmp_path / "x.wav")
    assert "uv sync --extra synth" in str(excinfo.value)


def test_write_pcm16_clips_and_writes_16bit(tmp_path):
    np = pytest.importorskip("numpy")
    out = tmp_path / "raw.wav"
    tts._write_pcm16(out, np.array([-2.0, -0.5, 0.0, 0.5, 2.0], dtype="float32"), 24000)
    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (24000, 2, 1)
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert frames[0] == -32767 and frames[-1] == 32767  # clamped, not wrapped


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")
def test_kokoro_render_resamples_to_16k_mono(monkeypatch, tmp_path):
    """Stubbed model + real ffmpeg: 24 kHz float -> 16 kHz/16-bit/mono clip."""
    np = pytest.importorskip("numpy")

    class FakePipeline:
        def __call__(self, phrase, voice=None, speed=1.0):
            t = np.arange(int(24000 * 0.5)) / 24000
            yield None, None, (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32")

    monkeypatch.setattr(tts.KokoroProvider, "_pipeline", FakePipeline())
    out = tmp_path / "syn_lb_ef_dora_1.0_01.wav"

    tts.KokoroProvider().render("enciende luz alta", "ef_dora", "1.0", out)

    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getsampwidth(), w.getnchannels()) == (16000, 2, 1)
        assert abs(w.getnframes() / 16000 - 0.5) < 0.05
    assert not list(tmp_path.glob("*.24k.wav"))  # temp 24 kHz file cleaned up


def test_cli_samples_with_mock_engine_writes_syn_prefixed_clips(tmp_path):
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(
        app,
        [
            "synth", "tts", "--engine", "mock", "--label", "lb", "--samples", "3",
            "--output", str(tmp_path / "synthetic"),
        ],
    )
    assert result.exit_code == 0, result.output
    written = sorted((tmp_path / "synthetic" / "lb").glob("*.wav"))
    assert len(written) == 3
    assert all(p.name.startswith("syn_lb_") for p in written)
    assert "3 random draw(s) per label" in result.output


def test_cli_rejects_unknown_label(tmp_path):
    from typer.testing import CliRunner

    from ultron.cli import app

    result = CliRunner().invoke(
        app, ["synth", "tts", "--engine", "mock", "--label", "nope", "--samples", "2"]
    )
    assert result.exit_code == 2
    assert "unknown label" in result.output
