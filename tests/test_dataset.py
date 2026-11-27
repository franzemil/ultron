"""Tests for dataset tooling (FR-020, FR-021, FR-022) and registry (FR-016)."""

import os
import shutil
import wave

import pytest

from ultron.dataset import ingest, validate, dc_fix, validate_registry

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
VALID_DIR = os.path.join(FIXTURES, "valid")
CORRUPT_DIR = os.path.join(FIXTURES, "corrupt")


def test_validate_passes_on_valid_fixture():
    assert validate(VALID_DIR) == 0


def test_validate_fails_loudly_on_corrupt_fixture(capsys):
    code = validate(CORRUPT_DIR)
    assert code != 0
    out = capsys.readouterr()
    assert "FAIL:" in out.out or "FAIL:" in out.err
    assert "rate_8k.wav" in out.out or "rate_8k.wav" in out.err
    assert "stereo.wav" in out.out or "stereo.wav" in out.err
    assert "8bit.wav" in out.out or "8bit.wav" in out.err


def test_validate_nonexistent_path():
    assert validate("/nonexistent/path") == 2


def test_ingest_copies_and_labels(tmp_path):
    src = os.path.join(FIXTURES, "valid")
    dest = str(tmp_path / "dataset")
    n = ingest(src, "lm", "ext", dest)
    assert n == 1
    expected = os.path.join(dest, "originals", "lm", "lm_ext_01.wav")
    assert os.path.isfile(expected)

    # second ingest increments the nn
    n2 = ingest(src, "lm", "ext", dest)
    assert n2 == 1
    assert os.path.isfile(os.path.join(dest, "originals", "lm", "lm_ext_02.wav"))


def test_ingest_rejects_bad_mic():
    with pytest.raises(SystemExit):
        ingest(FIXTURES, "lm", "bluetooth", "/tmp/x")


def test_ingest_rejects_bad_label_slug():
    with pytest.raises(SystemExit):
        ingest(FIXTURES, "Bad Label", "ext", "/tmp/x")


def test_dcfix_recenters_waveform(tmp_path):
    src = os.path.join(FIXTURES, "valid")
    dst = str(tmp_path / "dcfix.wav")
    shutil.copyfile(os.path.join(src, "valid.wav"), dst)

    # introduce a DC offset: add 1000 to every sample
    with wave.open(dst, "rb") as w:
        nch, sw, fs, nframes = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        frames = w.readframes(nframes)
    import array
    samples = array.array("h")
    samples.frombytes(frames)
    offset = 1000
    shifted = array.array("h", (max(-32768, min(32767, s + offset)) for s in samples))
    with wave.open(dst, "wb") as w:
        w.setnchannels(nch); w.setsampwidth(sw); w.setframerate(fs)
        w.writeframes(shifted.tobytes())

    dc_fix(tmp_path)

    with wave.open(dst, "rb") as w:
        frames = w.readframes(w.getnframes())
    samples = array.array("h"); samples.frombytes(frames)
    mean = sum(samples) / len(samples)
    assert abs(mean) < 50, f"DC offset not removed, mean={mean}"


def test_registry_validates():
    ok, errors = validate_registry("model/registry.json")
    assert ok, errors


def test_registry_rejects_bad(tmp_path):
    bad = os.path.join(tmp_path, "registry.json")
    with open(bad, "w") as f:
        f.write('{"version": 2, "language": "es"}')
    ok, errors = validate_registry(bad)
    assert not ok
    assert errors


# ── single-target scope: the "pi" mic tag is retired (owner decision 2026-11-26) ──


def test_pi_is_not_a_valid_mic_tag():
    from ultron.dataset import LEGACY_MICS, VALID_MICS

    assert "pi" not in VALID_MICS
    assert VALID_MICS == {"xiao", "ext"}
    assert LEGACY_MICS["pi"] == "ext"


def test_retag_pi_renames_legacy_recordings(tmp_path):
    """Existing *_pi_*.wav data must stay usable after the Pi target was removed."""
    from ultron.dataset import retag_pi

    label_dir = tmp_path / "originals" / "lb"
    label_dir.mkdir(parents=True)
    legacy = label_dir / "lb_pi_03.wav"
    legacy.write_bytes(b"RIFF")
    clash = label_dir / "lb2_pi_01.wav"
    clash.write_bytes(b"RIFF")
    (label_dir / "lb2_ext_01.wav").write_bytes(b"RIFF")  # target already exists

    # dry run touches nothing: one renameable, one blocked by an existing target
    assert retag_pi(tmp_path) == {"renamed": 1, "skipped": 1}
    assert legacy.is_file() and clash.is_file()

    assert retag_pi(tmp_path, apply=True) == {"renamed": 1, "skipped": 1}
    assert (label_dir / "lb_ext_03.wav").is_file()
    assert not legacy.exists()
    assert clash.is_file()  # skipped: lb2_ext_01.wav already there
