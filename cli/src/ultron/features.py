"""Feature extraction → feature archive — T7 (spec 002-can-think).

``ultron features`` walks the dataset tree and writes ``build/features.npz``:
one compressed NumPy archive holding the whole training set so PyTorch can
load it in one step — a float32 MFCC feature matrix, each row's label, and
each row's source file path — plus a split hash so training (T8) can verify
that its seeded split is reproducible (NFR-006).

Archive layout (keys written in this fixed order):

    features   : float32 (N, 100, 13) — one row per 2.0 s analysis window
                 (100 frames x 13 MFCC coefficients, TD-006), extracted
                 through the shared C MFCC binding (dsp/mfcc.py — the same
                 C bytes the devices run; no NumPy shortcut)
    labels     : str (N,) — registry label of the source clip
    paths      : str (N,) — source file path relative to the dataset root,
                 posix-style, e.g. ``originals/lb/lb_xiao_01.wav``
    split_hash : str ()  — sha256 over the ordered row manifest
                 (``"label\\tpath\\twindow_start\\n"`` per row, in row
                 order). Same tree (+ same --with-synthetic) ⇒ same hash; a
                 training rerun can assert its split input is unchanged.

Dataset policy (spec 001):

    originals/ + augmented/  → included (that IS the training set)
    holdout/                 → NEVER included (fresh hold-out, NFR-003 —
                               isolation is structural, by location)
    takes/                   → never (intermediate long takes)
    synthetic/               → only with ``--with-synthetic`` (default off;
                               the owner decides per run)

Windowing (TD-006, matches ``ultron infer``): 2.0 s windows (32000 samples)
hopped by 500 ms (8000 samples). Clips shorter than one window — e.g. the
per-utterance slices ``ultron split`` produces — are zero-padded to a single
full window, the same way the device sees a short phrase sitting inside its
sliding window.

DC offset + NaN (FR-022, spec 001): features are computed on DC-offset-
corrected samples — the same correction ``dataset dcfix`` applies, done in
memory (subtract the mean, round half-to-even, clip to int16), so it is
idempotent with an already-dcfixed tree. Any NaN feature row fails the
pipeline loudly, naming the file and window — no silent training on corrupt
data (lampara-aliz lesson).

Determinism: clips are visited in sorted order, the row manifest is derived
only from (label, path, window start), and the zip entries carry a fixed
timestamp — ``np.savez_compressed`` would embed the wall clock and break
byte-identical re-runs. Re-running over the same tree produces a
byte-identical archive.
"""

from __future__ import annotations

import hashlib
import sys
import wave
import zipfile
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np

from ultron.dataset import REGISTRY_IDS, is_synthetic

try:  # pytest runs with pythonpath=["."] — the repo root is importable.
    from dsp.mfcc import Mfcc
except ImportError:  # pragma: no cover — only hit via the console script
    # The `ultron` console script does not put the repo root on sys.path, so
    # the shared C binding is not importable from `ultron features` without
    # help. cli is installed editable from cli/src, so the repo root (where
    # dsp/ lives) is exactly three levels up from this file.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from dsp.mfcc import Mfcc

# MFCC / windowing parameters (dsp/mfcc.h + design.md TD-006).
SAMPLE_RATE = 16000
FRAME_LEN = 320                 # 20 ms frame
NUM_COEFFS = 13
WINDOW_FRAMES = 100             # 2.0 s analysis window
WINDOW_LEN = FRAME_LEN * WINDOW_FRAMES   # 32000 samples
HOP_LEN = 8000                  # 500 ms hop

# Dataset subdirectories that feed the archive, in fixed order.
SOURCES = ("originals", "augmented")
SYNTHETIC_SOURCE = "synthetic"

# Zip entries are stamped with the earliest representable timestamp so the
# archive bytes depend only on the arrays (see module docstring).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Archive key order — fixed, deterministic.
_ARCHIVE_KEYS = ("features", "labels", "paths", "split_hash")


class FeaturesError(RuntimeError):
    """Loud failure — no silent training on corrupt data (FR-022)."""


@dataclass(frozen=True)
class _Row:
    """One archive row: one analysis window of one source clip."""

    label: str
    path: str  # posix-style, relative to the dataset root
    start: int  # window start sample within the source clip
    features: np.ndarray  # (WINDOW_FRAMES, NUM_COEFFS) float32


def _load_wav(path: Path, rel: str) -> np.ndarray:
    """Read one clip as int16 samples, enforcing the 16 kHz/16-bit/mono contract.

    FR-021 format violations and unreadable/empty clips are loud failures.
    """
    try:
        with wave.open(str(path), "rb") as w:
            framerate, sampwidth, channels = (
                w.getframerate(),
                w.getsampwidth(),
                w.getnchannels(),
            )
            if (framerate, sampwidth, channels) != (SAMPLE_RATE, 2, 1):
                raise FeaturesError(
                    f"{rel}: expected 16000Hz/16bit/1ch, "
                    f"got {framerate}Hz/{sampwidth * 8}bit/{channels}ch "
                    "(run `ultron dataset validate`)"
                )
            n_frames = w.getnframes()
            raw = w.readframes(n_frames)
    except wave.Error as exc:
        raise FeaturesError(f"{rel}: unreadable WAV ({exc})") from exc
    if n_frames == 0:
        raise FeaturesError(f"{rel}: empty clip (0 frames)")
    return np.frombuffer(raw, dtype="<i2").astype(np.int32)


def _dc_correct(samples: np.ndarray) -> np.ndarray:
    """FR-022: features must come from DC-offset-corrected clips.

    Same semantics as ``dataset dcfix`` (subtract the mean, round
    half-to-even, clip back to int16), applied in memory — idempotent with an
    already-dcfixed tree.
    """
    offset = samples.mean()
    return np.clip(np.rint(samples - offset), -32768, 32767).astype(np.int16)


def _window_starts(n_samples: int) -> list[int]:
    """Window start samples: 0, 500 ms, 1.0 s, ... while a full 2 s window fits.

    A clip shorter than one window yields a single zero-padded window (see
    module docstring).
    """
    if n_samples < WINDOW_LEN:
        return [0]
    last = n_samples - WINDOW_LEN
    return list(range(0, last + 1, HOP_LEN))


def _window_features(window: np.ndarray, mfcc: Mfcc) -> np.ndarray:
    """(WINDOW_FRAMES, NUM_COEFFS) float32 for one window, via the C binding."""
    out = np.empty((WINDOW_FRAMES, NUM_COEFFS), dtype=np.float32)
    for i in range(WINDOW_FRAMES):
        frame = window[i * FRAME_LEN : (i + 1) * FRAME_LEN]
        out[i] = mfcc.compute(frame)
    return out


def collect_clips(dataset_root: Path, with_synthetic: bool = False) -> list[tuple[str, Path]]:
    """Deterministic (label, path) list of clips feeding the archive.

    Walks originals/ + augmented/ (+ synthetic/ only when asked). The label is
    the label directory name under each source (the tree IS the labeling);
    nested subdirectories (e.g. synthetic/noise/car/) are included with their
    label-dir label. Unknown labels and synthetic-tagged clips outside
    synthetic/ fail loudly.
    """
    root = Path(dataset_root)
    sources = list(SOURCES) + ([SYNTHETIC_SOURCE] if with_synthetic else [])
    clips: list[tuple[str, Path]] = []
    unknown: list[str] = []
    misplaced_synthetic: list[str] = []

    for source in sources:
        base = root / source
        if not base.is_dir():
            continue
        stray = [p for p in base.glob("*.wav")]
        if stray:
            raise FeaturesError(
                f"unlabeled clip(s) directly under {source}/ (the tree is the "
                "labeling — move them into a label directory): "
                + ", ".join(p.name for p in sorted(stray))
            )
        for label_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            label = label_dir.name
            if label not in REGISTRY_IDS:
                unknown.append(f"{source}/{label}")
                continue
            for wav in sorted(label_dir.rglob("*.wav")):
                if not with_synthetic and is_synthetic(wav):
                    # Policy (spec 001): synthetic never silently enters
                    # training. Inside originals/augmented it is a mistake.
                    misplaced_synthetic.append(wav.relative_to(root).as_posix())
                    continue
                clips.append((label, wav))

    if misplaced_synthetic:
        raise FeaturesError(
            "synthetic-tagged clip(s) outside synthetic/ — move them (they "
            "enter training only via --with-synthetic): "
            + ", ".join(sorted(misplaced_synthetic))
        )
    if unknown:
        raise FeaturesError(
            "unknown label dir(s) under the dataset (not in "
            f"model/registry.json): {', '.join(sorted(unknown))}; "
            f"valid labels: {', '.join(sorted(REGISTRY_IDS))}"
        )

    clips.sort(key=lambda item: item[1].relative_to(root).as_posix())
    return clips


def _split_hash(rows: list[_Row]) -> str:
    """sha256 over the ordered row manifest — the split input fingerprint."""
    manifest = "".join(f"{r.label}\t{r.path}\t{r.start}\n" for r in rows)
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def save_npz_deterministic(path: Path, arrays: dict[str, np.ndarray]) -> None:
    """Write ``arrays`` as a compressed .npz with byte-reproducible output.

    Same format ``np.load`` expects, but with a fixed entry order and fixed
    zip entry timestamps: ``np.savez_compressed`` stamps each entry with the
    current wall clock, so two runs over the same tree would produce
    different bytes (T7 verify: re-run ⇒ identical file hash).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in _ARCHIVE_KEYS:
            buf = BytesIO()
            np.lib.format.write_array(buf, arrays[name], allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, buf.getvalue())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clip_rows(root: Path, label: str, clip_paths, mfcc: Mfcc) -> tuple[list[_Row], list[str]]:
    """Rows for the given clips: DC-corrected C-MFCC windows + NaN failures.

    Short clips yield one zero-padded window (see :func:`_window_starts`).
    NaN rows are returned as failure strings (caller decides when to raise
    — build_archive collects across ALL clips first so the error names every
    corrupt file, FR-022: no silent training on corrupt data).
    """
    rows: list[_Row] = []
    nan_failures: list[str] = []
    for path in clip_paths:
        rel = path.relative_to(root).as_posix()
        samples = _dc_correct(_load_wav(path, rel))
        for start in _window_starts(len(samples)):
            if len(samples) < WINDOW_LEN:  # short clip: one padded window
                window = np.zeros(WINDOW_LEN, dtype=np.int16)
                window[: len(samples)] = samples
            else:
                window = samples[start : start + WINDOW_LEN]
            feats = _window_features(window, mfcc)
            if np.isnan(feats).any():
                nan_failures.append(f"{rel} @ {start / SAMPLE_RATE:.2f}s")
                continue
            rows.append(_Row(label=label, path=rel, start=start, features=feats))
    return rows, nan_failures


def _nan_error(nan_failures: list[str]) -> FeaturesError:
    shown = nan_failures[:10]
    more = "" if len(nan_failures) <= 10 else f" (+{len(nan_failures) - 10} more)"
    return FeaturesError(
        "NaN feature row(s) — refusing to train on corrupt data (FR-022, "
        f"spec 001): {', '.join(shown)}{more}"
    )


def build_archive(
    dataset_root: Path | str = "dataset",
    out_path: Path | str = "build/features.npz",
    with_synthetic: bool = False,
    mfcc: Optional[Mfcc] = None,
) -> dict:
    """Extract features for the whole training set → one deterministic .npz.

    Returns a summary dict (out path, clip/row counts, per-label counts,
    split hash, archive sha256). Raises FeaturesError loudly on format
    violations, unknown labels, or NaN feature rows (FR-022) — and writes
    nothing in that case.
    """
    root = Path(dataset_root)
    out = Path(out_path)

    clips = collect_clips(root, with_synthetic=with_synthetic)
    if not clips:
        scanned = ", ".join(SOURCES + ((SYNTHETIC_SOURCE,) if with_synthetic else ()))
        raise FeaturesError(
            f"no wav clips found under {root}/{{{scanned}}} — record or ingest "
            "first (`ultron record`, `ultron dataset ingest`)"
        )

    rows: list[_Row] = []
    nan_failures: list[str] = []
    own_mfcc = mfcc is None
    if own_mfcc:
        try:
            mfcc = Mfcc(SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001 — surface any build/load failure loudly
            raise FeaturesError(
                f"cannot load the shared C MFCC library (dsp/build/libmfcc.so): {exc}"
            ) from exc

    try:
        for label, path in clips:
            clip_rows, nan = _clip_rows(root, label, [path], mfcc)
            rows.extend(clip_rows)
            nan_failures.extend(nan)
    finally:
        if own_mfcc and mfcc is not None:
            mfcc.close()

    if nan_failures:
        raise _nan_error(nan_failures)

    arrays = {
        "features": np.stack([r.features for r in rows]).astype(np.float32),
        "labels": np.array([r.label for r in rows], dtype=np.str_),
        "paths": np.array([r.path for r in rows], dtype=np.str_),
        "split_hash": np.array(_split_hash(rows), dtype=np.str_),
    }
    save_npz_deterministic(out, arrays)

    label_clips = Counter(label for label, _ in clips)
    label_rows = Counter(r.label for r in rows)
    return {
        "out": out,
        "n_clips": len(clips),
        "n_rows": len(rows),
        "clips_per_label": dict(sorted(label_clips.items())),
        "rows_per_label": dict(sorted(label_rows.items())),
        "split_hash": arrays["split_hash"].item(),
        "sha256": _sha256_file(out),
        "bytes": out.stat().st_size,
    }


def extract_holdout(
    dataset_root: Path | str = "dataset",
    labels: Optional[set[str]] = None,
    mfcc: Optional[Mfcc] = None,
) -> dict:
    """Extract fresh hold-out windows for training-time scoring (T8, NFR-003).

    Walks ``dataset/holdout/<label>/`` — the structurally isolated fresh
    hold-out (spec 001) that NEVER enters the archive — with exactly the same
    DC-corrected C-MFCC windowing path as :func:`build_archive`. NaN rows
    fail loudly (FR-022) and synthetic-tagged clips are rejected (spec 001:
    synthetic never enters the fresh hold-out).

    ``labels`` optionally restricts extraction to the trained class set
    (FR-032) — hold-out dirs of not-yet-trained labels are skipped so they
    cannot pollute the fresh hold-out report.

    Returns ``{"features": (M, 100, 13) float32, "labels": (M,),
    "paths": (M,), "windows_per_label": {...}, "clips_per_label": {...}}``.
    """
    root = Path(dataset_root)
    base = root / "holdout"
    if not base.is_dir():
        raise FeaturesError(
            f"no fresh hold-out directory: {base} — NFR-003 requires hold-out "
            "scoring (record clips under dataset/holdout/<label>/)"
        )

    rows: list[_Row] = []
    nan_failures: list[str] = []
    clip_counts: Counter[str] = Counter()
    unknown: list[str] = []
    own_mfcc = mfcc is None
    if own_mfcc:
        try:
            mfcc = Mfcc(SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001 — surface any build/load failure loudly
            raise FeaturesError(
                f"cannot load the shared C MFCC library (dsp/build/libmfcc.so): {exc}"
            ) from exc
    try:
        for label_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            label = label_dir.name
            if label not in REGISTRY_IDS:
                unknown.append(f"holdout/{label}")
                continue
            if labels is not None and label not in labels:
                continue  # not in the trained subset (FR-032) — skip, don't pollute
            wavs = sorted(label_dir.rglob("*.wav"))
            if not wavs:
                continue
            stray_synthetic = [w for w in wavs if is_synthetic(w)]
            if stray_synthetic:
                raise FeaturesError(
                    "synthetic-tagged clip(s) in the fresh hold-out (spec 001: "
                    "synthetic never enters the fresh hold-out): "
                    + ", ".join(
                        w.relative_to(root).as_posix() for w in sorted(stray_synthetic)
                    )
                )
            clip_counts[label] = len(wavs)
            clip_rows, nan = _clip_rows(root, label, wavs, mfcc)
            rows.extend(clip_rows)
            nan_failures.extend(nan)
    finally:
        if own_mfcc and mfcc is not None:
            mfcc.close()

    if nan_failures:
        raise _nan_error(nan_failures)
    if unknown:
        raise FeaturesError(
            "unknown label dir(s) under holdout/ (not in "
            f"model/registry.json): {', '.join(sorted(unknown))}"
        )
    if not rows:
        raise FeaturesError(
            f"no hold-out wav clips under {base}/<label>/ — NFR-003 requires "
            "fresh hold-out scoring (record clips under dataset/holdout/<label>/)"
        )

    windows_per_label = Counter(r.label for r in rows)
    return {
        "features": np.stack([r.features for r in rows]).astype(np.float32),
        "labels": np.array([r.label for r in rows], dtype=np.str_),
        "paths": np.array([r.path for r in rows], dtype=np.str_),
        "windows_per_label": dict(sorted(windows_per_label.items())),
        "clips_per_label": dict(sorted(clip_counts.items())),
    }
