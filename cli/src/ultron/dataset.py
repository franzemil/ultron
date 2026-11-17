"""Dataset tooling: ingest, validate, dcfix, registry validation."""

import array
import math
import os
import re
import shutil
import sys
import wave

# Mic source tags allowed on `dataset ingest` (FR-020).
VALID_MICS = {"xiao", "pi", "ext"}

# Label slug rule: lowercase ASCII, must start with a letter (label slugs).
LABEL_RE = re.compile(r"^[a-z][a-z0-9]*$")

# Full set of ids allowed in the command registry: 10 actions + cancel (cp),
# plus wake and noise. Registered via validate_registry (FR-016).
REGISTRY_IDS = {"wake", "noise", "lm", "lb", "la", "gd", "gi", "p", "cp", "lpb", "lpa", "a", "cc"}


def _fail(msg):
    """Print an error message to stderr and exit non-zero."""
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _iter_wavs(root):
    """Yield every .wav path under <root>, walking the filesystem tree."""
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.lower().endswith(".wav"):
                yield os.path.join(dirpath, name)


def _wav_info(path):
    """Return a dict of WAV parameters (channels, sampwidth, framerate, nframes)."""
    with wave.open(path, "rb") as w:
        return {
            "channels": w.getnchannels(),
            "sampwidth": w.getsampwidth(),
            "framerate": w.getframerate(),
            "nframes": w.getnframes(),
        }


def _next_nn(label_dir, label, mic):
    """Return the next free two-digit sequence number for {label}_{mic}_{nn}.wav."""
    prefix = f"{label}_{mic}_"
    best = 0
    for name in os.listdir(label_dir):
        if not (name.startswith(prefix) and name.endswith(".wav")):
            continue
        try:
            nn = int(name[len(prefix):-4])
        except ValueError:
            continue
        if nn > best:
            best = nn
    return best + 1


def ingest(source, label, mic, dest):
    """Copy and label WAVs from ANY source into dataset/originals/<label>/.

    FR-020: device recordings and owner-supplied external recordings alike,
    tagging the mic source in the filename (lm_xiao_01.wav / lm_ext_01.wav).
    """
    if mic not in VALID_MICS:
        _fail(f"invalid mic '{mic}'; must be one of {sorted(VALID_MICS)}")
    if not LABEL_RE.match(label):
        _fail(f"invalid label slug '{label}'; lowercase ASCII slug required")
    if not os.path.isdir(source):
        _fail(f"source not a directory: {source}")

    label_dir = os.path.join(dest, "originals", label)
    os.makedirs(label_dir, exist_ok=True)

    nn = _next_nn(label_dir, label, mic)
    copied = 0
    for src in _iter_wavs(source):
        name = f"{label}_{mic}_{nn:02d}.wav"
        shutil.copy2(src, os.path.join(label_dir, name))
        nn += 1
        copied += 1

    print(f"ingested {copied} wav(s) into {label_dir}")
    return copied


def validate(path):
    """Return exit code: 0 if all wavs are 16 kHz/16-bit/mono, 1 if any fail.

    FR-021: check every clip is 16 kHz/16-bit/mono; exit non-zero listing
    each failing file. A nonexistent path returns exit code 2.
    """
    if not os.path.isdir(path):
        print(f"error: not a directory: {path}", file=sys.stderr)
        return 2

    failures = []
    count = 0
    for wav in _iter_wavs(path):
        count += 1
        try:
            info = _wav_info(wav)
        except Exception as exc:  # noqa: BLE001 — report any unreadable file as a failure
            failures.append((wav, f"unreadable: {exc}"))
            continue
        ok = (
            info["framerate"] == 16000
            and info["sampwidth"] == 2
            and info["channels"] == 1
        )
        if not ok:
            failures.append(
                (wav, f"{info['framerate']}Hz/{info['sampwidth'] * 8}bit/{info['channels']}ch")
            )

    print(f"checked {count} wav(s) under {path}")
    for wav, detail in failures:
        print(f"FAIL: {wav} ({detail})")
    if failures:
        print(f"{len(failures)} file(s) failed validation", file=sys.stderr)
        return 1
    print("all wavs valid (16 kHz / 16-bit / mono)")
    return 0


def dc_fix(path):
    """Remove DC offset (subtract mean) from every WAV under <path>, in place.

    WHY: microphones (especially the XIAO PDM mic and cheap USB mics) can add a
    constant voltage to every recorded sample, shifting the whole waveform up or
    down away from zero -- that is "DC offset". If left in place, every MFCC
    coefficient is computed on a skewed signal, so features drift from what the
    on-device DSP actually produces and training silently degrades
    (lampara-aliz lesson: NaN / DSP error -5, no silent training on corrupt data).

    WHAT: subtracts that constant (the waveform mean) from each int16 sample,
    recentering the signal around zero before feature extraction (FR-022).

    Non-16-bit or unreadable files are skipped with a stderr warning.
    """
    count = 0
    for wav in _iter_wavs(path):
        try:
            info = _wav_info(wav)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {wav}: unreadable ({exc})", file=sys.stderr)
            continue
        if info["sampwidth"] != 2:
            print(f"skip {wav}: not 16-bit", file=sys.stderr)
            continue

        with wave.open(wav, "rb") as w:
            frames = w.readframes(w.getnframes())

        samples = array.array("h")
        samples.frombytes(frames)
        if not samples:
            continue
        offset = sum(samples) / len(samples)
        fixed = array.array(
            "h",
            (max(-32768, min(32767, round(s - offset))) for s in samples),
        )

        with wave.open(wav, "wb") as w:
            w.setnchannels(info["channels"])
            w.setsampwidth(info["sampwidth"])
            w.setframerate(info["framerate"])
            w.writeframes(fixed.tobytes())
        count += 1

    print(f"dc-fixed {count} wav(s) under {path}")


# ---------------------------------------------------------------------------
# registry.json schema + validator (FR-016)
# ---------------------------------------------------------------------------

# Top-level keys required in model/registry.json (versioned; D-005).
REGISTRY_SCHEMA = {
    "required": {"version", "language", "wake", "noise", "commands", "cap"},
    "command_fields": {
        "id", "sigla", "phrase", "behavior", "channels",
    },
}

# The 10 action command ids (cancel `cp` is handled separately below).
ACTION_IDS = {"lm", "lb", "la", "gd", "gi", "p", "lpb", "lpa", "a", "cc"}
CANCEL_ID = "cp"


def validate_registry(path="model/registry.json"):
    """Validate registry.json against the FR-016 schema. Returns (ok, errors).

    Enforces: version 2, language "es", wake + noise entries, exactly the
    10 action commands plus cancel (cp), each with id/sigla/phrase/behavior/
    channels, and cap.actions == 10 / cap.model_classes == 13.
    """
    import json

    errors = []
    if not os.path.isfile(path):
        return False, [f"registry not found: {path}"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return False, [f"invalid JSON: {exc}"]

    if not isinstance(data, dict):
        return False, ["registry root must be an object"]

    for key in REGISTRY_SCHEMA["required"]:
        if key not in data:
            errors.append(f"missing top-level key: {key}")

    if data.get("version") != 2:
        errors.append("registry version must be 2")
    if data.get("language") != "es":
        errors.append('registry language must be "es"')

    wake = data.get("wake")
    if not isinstance(wake, dict) or wake.get("id") != "wake":
        errors.append('wake entry must have id "wake"')
    noise = data.get("noise")
    if not isinstance(noise, dict) or noise.get("id") != "noise":
        errors.append('noise entry must have id "noise"')

    commands = data.get("commands")
    if not isinstance(commands, list):
        errors.append("commands must be a list")
    else:
        seen = set()
        for cmd in commands:
            if not isinstance(cmd, dict):
                errors.append("each command must be an object")
                continue
            for field in REGISTRY_SCHEMA["command_fields"]:
                if field not in cmd:
                    errors.append(f"command missing field: {field}")
            cid = cmd.get("id")
            if cid in ACTION_IDS or cid == CANCEL_ID:
                if cid in seen:
                    errors.append(f"duplicate command id: {cid}")
                seen.add(cid)
            else:
                errors.append(f"unknown command id: {cid!r}")
        if seen - ACTION_IDS != {CANCEL_ID}:
            errors.append(
                f"command ids must be the 10 actions plus cancel ({CANCEL_ID}), "
                f"got {sorted(seen)}"
            )
        missing = ACTION_IDS - seen
        if missing:
            errors.append(f"missing action command ids: {sorted(missing)}")

    cap = data.get("cap")
    if not isinstance(cap, dict):
        errors.append("cap must be an object")
    else:
        if cap.get("actions") != 10:
            errors.append("cap.actions must be 10")
        if cap.get("model_classes") != 13:
            errors.append("cap.model_classes must be 13")

    return (len(errors) == 0), errors


if __name__ == "__main__":
    raise SystemExit(validate(sys.argv[1] if len(sys.argv) > 1 else "dataset"))


# ---------------------------------------------------------------------------
# dataset export / import (zip) — persist recordings beyond the worktree
# ---------------------------------------------------------------------------
def export_zip(path="dataset", out="dataset_export.zip"):
    """Zip the dataset directory (takes + originals + everything under <path>).

    Returns (out, file_count). Refuses to overwrite without force.
    """
    import zipfile

    path = os.path.normpath(path)
    if not os.path.isdir(path):
        _fail(f"not a directory: {path}")
    if os.path.isfile(out):
        _fail(f"output already exists: {out} (remove it first)")

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, files in os.walk(path):
            for name in sorted(files):
                full = os.path.join(dirpath, name)
                arc = os.path.relpath(full, os.path.dirname(path) or ".")
                z.write(full, arc)
                count += 1
    print(f"exported {count} file(s) -> {out}")
    return out, count


def import_zip(zip_path, dest="dataset", force=False):
    """Import a dataset zip produced by export_zip into <dest>.

    Refuses to overwrite existing files unless force=True.
    Returns (dest, imported_count).
    """
    import zipfile

    if not os.path.isfile(zip_path):
        _fail(f"zip not found: {zip_path}")

    # zip root: export uses os.path.relpath(full, dirname(path)); when path is
    # "dataset" the arcnames start with "dataset/...". Strip the first component
    # if it matches dest's basename so imports are flexible about naming.
    imported = 0
    overwritten = 0
    with zipfile.ZipFile(zip_path, "r") as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        if not names:
            _fail(f"zip contains no files: {zip_path}")
        first = names[0].split("/")[0]
        base = os.path.basename(os.path.normpath(dest))
        for name in names:
            parts = name.split("/")
            if parts[0] in ("..", ".") or name.startswith("/"):
                continue  # zip-slip guard: skip suspicious paths
            if parts[0] == first and first != base:
                rel = "/".join(parts[1:])
            else:
                rel = name
            if not rel or rel.startswith(".."):
                continue
            target = os.path.join(dest, rel)
            if os.path.isfile(target):
                if not force:
                    print(f"skip existing: {target} (use --force to overwrite)")
                    continue
                overwritten += 1
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            imported += 1
    print(f"imported {imported} file(s) into {dest} ({overwritten} overwritten)")
    return dest, imported
