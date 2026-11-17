"""Host-side checks for `ultron dataset export` / `import` (zip persistence).

Verifies:
  * export zips every file under the dataset dir
  * import restores files (16k/16-bit/mono WAVs stay byte-identical)
  * existing files are skipped without --force, overwritten with it
  * missing dirs / zips fail fast (exit non-zero)

Run:  uv run pytest tests/test_dataset_zip.py
"""

from __future__ import annotations

import os
import wave
import zipfile

import pytest

from ultron.dataset import export_zip, import_zip


def _make_wav(path, seconds=1.0, sr=16000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = bytes(os.urandom(int(sr * seconds * 2)))  # 16-bit
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return path


def test_export_import_roundtrip(tmp_path):
    ds = tmp_path / "dataset"
    originals = ds / "originals" / "lb"
    takes = ds / "takes"
    w1 = _make_wav(str(originals / "lb_local_01.wav"))
    w2 = _make_wav(str(takes / "take_01_lb.wav"))
    raw1 = open(w1, "rb").read()

    out = str(tmp_path / "export.zip")
    _, count = export_zip(path=str(ds), out=out)
    assert count == 2
    assert os.path.isfile(out)

    # Import into a fresh dest
    dest = tmp_path / "restored"
    _, imported = import_zip(out, dest=str(dest))
    assert imported == 2

    restored = dest / "originals" / "lb" / "lb_local_01.wav"
    assert restored.is_file()
    # Byte-identical WAV (dataset contract preserved)
    assert open(restored, "rb").read() == raw1
    assert (dest / "takes" / "take_01_lb.wav").is_file()

    # Restored WAV validates (16k/16-bit/mono)
    with wave.open(str(restored), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getsampwidth() == 2
        w.getnchannels() == 1


def test_import_skips_existing_without_force(tmp_path):
    out = str(tmp_path / "e.zip")
    ds = tmp_path / "dataset"
    _make_wav(str(ds / "originals" / "lb" / "lb_local_01.wav"))
    export_zip(path=str(ds), out=out)

    dest = tmp_path / "restored"
    target = dest / "originals" / "lb" / "lb_local_01.wav"
    os.makedirs(target.parent, exist_ok=True)
    target.write_bytes(b"existing content")

    _, imported = import_zip(out, dest=str(dest))
    assert imported == 0
    assert target.read_bytes() == b"existing content"

    _, imported = import_zip(out, dest=str(dest), force=True)
    assert imported == 1
    assert target.read_bytes() != b"existing content"


def test_export_fails_on_missing_dir(tmp_path):
    with pytest.raises(SystemExit) as exc:
        export_zip(path=str(tmp_path / "nope"), out=str(tmp_path / "x.zip"))
    assert exc.value.code == 1


def test_export_refuses_overwrite(tmp_path):
    ds = tmp_path / "dataset"
    _make_wav(str(ds / "originals" / "lb" / "lb_local_01.wav"))
    out = str(tmp_path / "e.zip")
    export_zip(path=str(ds), out=out)
    with pytest.raises(SystemExit) as exc:
        export_zip(path=str(ds), out=out)
    assert exc.value.code == 1


def test_import_fails_on_missing_zip(tmp_path):
    with pytest.raises(SystemExit) as exc:
        import_zip(str(tmp_path / "nope.zip"), dest=str(tmp_path / "d"))
    assert exc.value.code == 1


def test_import_skips_path_traversal(tmp_path):
    """Zip entries with .. are ignored (zip-slip guard)."""
    out = str(tmp_path / "evil.zip")
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("../evil.wav", b"nope")
    dest = tmp_path / "restored"
    _, imported = import_zip(out, dest=str(dest))
    assert imported == 0
    assert not (tmp_path / "evil.wav").exists()
