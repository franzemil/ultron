"""PyTorch training + threshold sweep — T8 (spec 002-can-think).

Hyper-parameters are pinned by design.md ("Model"): Adam 1e-3, sparse
categorical cross-entropy (NLLLoss on in-graph log-probs — the softmax stays
inside :class:`~ultron_model.model.KwsNet` so exports emit probabilities),
class weights for imbalance, batch 16, early stopping (patience 30),
stratified 90/10 split seeded 42, up to 300 epochs. The fresh hold-out
(``dataset/holdout`` — structurally isolated, spec 001) is scored **after**
training and reported separately from the val split (NFR-003 honesty rule:
val numbers are memorization context only).

Class-set derivation (FR-032): classes come from the labels present in the
feature archive — never assumed from the registry — requiring ``wake``,
``noise`` and ≥ 1 command class, failing with the **missing labels named**
(FR-033). Class order is frozen deterministically as ``wake, noise`` + the
registry's command order filtered to the labels present (re-frozen on every
retrain; T9 echoes it into ``build/labels.json``).

Phrase variants share one class per command (TD-010); per-variant recording
minimums are validated per label against the registry-derived variant
structure (NFR-005, spec 001): ≥ 20 originals per accepted phrase variant,
wake/noise ≥ 30 originals. Filenames carry no variant identity, so counts are
per label from ``originals/<label>/`` paths only — augmented and synthetic
clips never count (spec 001 policy).

Synthetic rows enter training only via ``with_synthetic=True`` (spec 001
T5.1 — default off at every step: ``ultron features`` excludes them from the
archive unless asked, and training excludes them from an archive unless
asked again).

Reproducibility (NFR-006): the split is derived from (seed, archive
split_hash) with a single-threaded, manually seeded torch — same seed +
same archive ⇒ identical split manifest, identical metrics, identical
threshold sweep.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
import torch
from torch import nn

from .model import KwsNet, NUM_COEFFS, NUM_FRAMES

# Design-pinned defaults (design.md "Model").
SEED = 42
MAX_EPOCHS = 300
BATCH_SIZE = 16
LEARNING_RATE = 1e-3
PATIENCE = 30
VAL_FRACTION = 0.1

# NFR-005 minimums (spec 001): originals per accepted phrase variant; wake
# and noise have no command variants and need ≥ 30 originals each.
MIN_PER_VARIANT = 20
WAKE_NOISE_MIN = 30

# Threshold sweep grid (design.md): threshold × margin against the hold-out.
THRESHOLD_GRID = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
MARGIN_GRID = (0.05, 0.10, 0.15, 0.20)
WINDOW_S = 5.0  # wake → command listening window (Q-006; runtime is spec 003)

# Spec-001 synthetic tags, mirrored here because ultron-model must not depend
# on the CLI package (dependency direction is cli → ultron-model).
_SYNTHETIC_DIR = "synthetic"
_SYNTHETIC_PREFIX = "syn_"


class TrainError(RuntimeError):
    """Loud failure — missing labels, short counts, corrupt archive, …"""


@dataclass(frozen=True)
class Archive:
    """Validated view of build/features.npz (built by T7)."""

    features: np.ndarray  # (N, 100, 13) float32
    labels: np.ndarray  # (N,) str
    paths: np.ndarray  # (N,) str — posix-style, relative to the dataset root
    split_hash: str  # sha256 over the ordered row manifest (T7)


def is_synthetic_path(path: str) -> bool:
    """Mirror of spec 001's synthetic tag (directory or filename prefix)."""
    p = str(path)
    return p.startswith(_SYNTHETIC_PREFIX) or f"{_SYNTHETIC_DIR}/" in p


# ---------------------------------------------------------------------------
# Archive / registry loading
# ---------------------------------------------------------------------------


def load_archive(path: Path | str) -> Archive:
    """Load + validate a T7 feature archive. Loud on any contract violation."""
    path = Path(path)
    if not path.is_file():
        raise TrainError(
            f"feature archive not found: {path} — run `ultron features` first (T7)"
        )
    try:
        with np.load(path, allow_pickle=False) as arch:
            missing = {"features", "labels", "paths", "split_hash"} - set(arch.files)
            if missing:
                raise TrainError(
                    f"{path}: archive is missing key(s) {sorted(missing)} — "
                    "not a T7 features.npz; rebuild with `ultron features`"
                )
            features = arch["features"]
            labels = arch["labels"]
            paths = arch["paths"]
            split_hash = str(arch["split_hash"])
    except TrainError:
        raise
    except Exception as exc:  # noqa: BLE001 — unreadable archives fail loudly
        raise TrainError(f"{path}: cannot read feature archive ({exc})") from exc

    if features.ndim != 3 or features.shape[1:] != (NUM_FRAMES, NUM_COEFFS):
        raise TrainError(
            f"{path}: features must be (N, {NUM_FRAMES}, {NUM_COEFFS}), got {features.shape}"
        )
    if features.dtype != np.float32:
        features = features.astype(np.float32)
    if features.shape[0] == 0:
        raise TrainError(f"{path}: archive is empty — nothing to train on")
    if np.isnan(features).any():
        bad = np.flatnonzero(np.isnan(features).any(axis=(1, 2)))[:10]
        raise TrainError(
            f"{path}: NaN feature row(s) — refusing to train on corrupt data "
            f"(FR-022): rows {bad.tolist()}"
        )
    if len(labels) != features.shape[0] or len(paths) != features.shape[0]:
        raise TrainError(
            f"{path}: features/labels/paths length mismatch "
            f"({features.shape[0]}/{len(labels)}/{len(paths)})"
        )
    return Archive(
        features=features,
        labels=labels.astype(str),
        paths=paths.astype(str),
        split_hash=split_hash,
    )


def load_registry(path: Path | str) -> dict:
    """Load model/registry.json (D-005) — the canonical command order."""
    path = Path(path)
    if not path.is_file():
        raise TrainError(f"registry not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            reg = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise TrainError(f"{path}: cannot parse registry ({exc})") from exc
    for key in ("wake", "noise", "commands"):
        if key not in reg:
            raise TrainError(f"{path}: registry is missing '{key}' (FR-016 schema)")
    return reg


def registry_command_ids(reg: dict) -> list[str]:
    """Command ids in registry order — the canonical class order (FR-013)."""
    return [str(cmd.get("id")) for cmd in reg["commands"] if cmd.get("id")]


def registry_phrase_count(reg: dict, label: str) -> Optional[int]:
    """Number of accepted phrase variants for a command (None if not a command)."""
    for cmd in reg["commands"]:
        if str(cmd.get("id")) == label:
            return len(cmd.get("phrase") or [])
    return None


# ---------------------------------------------------------------------------
# Class set (FR-032/033) + NFR-005 count validation
# ---------------------------------------------------------------------------


def derive_class_set(labels: Sequence[str], reg: dict) -> list[str]:
    """wake, noise + present commands in registry order (FR-032, TD-012).

    Raises TrainError naming every missing required label (FR-033): the set
    must contain `wake`, `noise`, and at least one command class.
    """
    present = set(str(l) for l in labels)
    known = {"wake", "noise", *registry_command_ids(reg)}
    unknown = sorted(present - known)
    if unknown:
        raise TrainError(
            f"archive contains label(s) not in model/registry.json: "
            f"{', '.join(unknown)}; valid labels: {', '.join(sorted(known))}"
        )

    missing = [req for req in ("wake", "noise") if req not in present]
    commands = [c for c in registry_command_ids(reg) if c in present]
    if missing and not commands:
        raise TrainError(
            "training set is missing required label(s): "
            f"{', '.join(missing + ['≥ 1 command class'])} — training requires "
            "`wake`, `noise` and at least one command (FR-033, TD-012); "
            f"registered commands: {', '.join(registry_command_ids(reg))}"
        )
    if missing:
        raise TrainError(
            "training set is missing required label(s): "
            f"{', '.join(missing)} — training requires `wake`, `noise` and at "
            "least one command (FR-033, TD-012)"
        )
    if not commands:
        raise TrainError(
            "training set has no command class — training requires `wake`, "
            "`noise` and at least one command (FR-033, TD-012); registered "
            f"commands: {', '.join(registry_command_ids(reg))}"
        )
    return ["wake", "noise", *commands]


def originals_clip_counts(labels: Sequence[str], paths: Sequence[str]) -> dict[str, int]:
    """Distinct original clips per label — augmented/synthetic never count.

    NFR-005 counts original recordings; the archive's rows are windows, so
    multiple windows of one clip collapse to one via its path.
    """
    clips: set[tuple[str, str]] = set()
    for label, path in zip(labels, paths):
        p = str(path)
        if p.startswith("originals/") and not is_synthetic_path(p):
            clips.add((str(label), p))
    return dict(sorted(Counter(label for label, _ in clips).items()))


def check_nfr005(
    counts: dict[str, int], classes: Sequence[str], reg: dict, min_originals: Optional[int] = None
) -> dict:
    """Validate per-label originals against registry-derived variant minimums.

    Returns the minimums dict for the report. Raises TrainError naming every
    short label with its count and minimum (variants × MIN_PER_VARIANT for
    commands; WAKE_NOISE_MIN for wake/noise).

    ``min_originals`` is the explicit per-run override (NFR-005 deviation,
    owner decides per run — same policy as --with-synthetic): every label's
    minimum becomes that value (0 skips the check entirely). The deviation is
    recorded in the report via ``nfr005_overridden``.
    """
    minimums: dict[str, int] = {}
    for label in classes:
        if min_originals is not None:
            minimums[label] = min_originals
        elif label in ("wake", "noise"):
            minimums[label] = WAKE_NOISE_MIN
        else:
            variants = registry_phrase_count(reg, label) or 1
            minimums[label] = MIN_PER_VARIANT * variants
    short = {
        label: (counts.get(label, 0), minimums[label])
        for label in classes
        if counts.get(label, 0) < minimums[label]
    }
    if short:
        detail = "; ".join(
            f"{label}: have {have}, need ≥ {need}" for label, (have, need) in sorted(short.items())
        )
        raise TrainError(
            "insufficient original recordings (NFR-005 — variants × 20 per "
            f"command, wake/noise ≥ {WAKE_NOISE_MIN}; augmented/synthetic never "
            f"count): {detail}"
        )
    return minimums


def filter_synthetic(
    features: np.ndarray, labels: np.ndarray, paths: np.ndarray, with_synthetic: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Drop synthetic-tagged rows unless training opted in (spec 001 T5.1).

    Returns (features, labels, paths, synthetic_rows_used).
    """
    keep = np.array(
        [with_synthetic or not is_synthetic_path(p) for p in paths], dtype=bool
    )
    syn_total = int(sum(1 for p in paths if is_synthetic_path(p)))
    used = syn_total if with_synthetic else 0
    return features[keep], labels[keep], paths[keep], used


# ---------------------------------------------------------------------------
# Seeded stratified split (NFR-006)
# ---------------------------------------------------------------------------


def _seed_from(seed: int, split_hash: str) -> int:
    digest = hashlib.sha256(f"{seed}|{split_hash}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def clip_split(
    labels: Sequence[str],
    paths: Sequence[str],
    split_hash: str,
    *,
    seed: int = SEED,
    val_fraction: float = VAL_FRACTION,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Stratified 90/10 split by CLIP — all windows of a clip stay together.

    Deterministic in (seed, archive split_hash): same archive + same seed ⇒
    identical split. Returns (train_idx, val_idx, split_manifest_hash) where
    the manifest hash fingerprints the row → side assignment.
    """
    clips: dict[tuple[str, str], list[int]] = {}
    for i, (label, path) in enumerate(zip(labels, paths)):
        clips.setdefault((str(label), str(path)), []).append(i)

    rng = np.random.default_rng(_seed_from(seed, split_hash))
    train_idx: list[int] = []
    val_idx: list[int] = []

    by_label: dict[str, list[tuple[str, list[int]]]] = {}
    for (label, path), idxs in clips.items():
        by_label.setdefault(label, []).append((path, idxs))
    for label in sorted(by_label):
        entries = sorted(by_label[label])  # sorted → shuffle is over a fixed order
        rng.shuffle(entries)
        n_val = 0 if len(entries) == 1 else max(1, round(len(entries) * val_fraction))
        for _p, idxs in entries[:n_val]:
            val_idx.extend(idxs)
        for _p, idxs in entries[n_val:]:
            train_idx.extend(idxs)

    if not train_idx or not val_idx:
        raise TrainError(
            "stratified split produced an empty train or val set — the archive "
            "has too few clips (need ≥ 2 clips per class)"
        )

    train_sorted, val_sorted = sorted(train_idx), sorted(val_idx)
    side = ["train"] * len(labels)
    for i in val_sorted:
        side[i] = "val"
    manifest = "".join(
        f"{label}\t{path}\t{side[i]}\n"
        for i, (label, path) in enumerate(zip(labels, paths))
    )
    manifest_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    return np.array(train_sorted), np.array(val_sorted), manifest_hash


# ---------------------------------------------------------------------------
# Training (design.md "Model")
# ---------------------------------------------------------------------------


def _class_weights(y: np.ndarray, num_classes: int) -> torch.Tensor:
    """Balanced inverse-frequency weights (n / (C · count_c))."""
    counts = np.bincount(y, minlength=num_classes).astype(np.float64)
    counts = np.maximum(counts, 1.0)
    weights = counts.sum() / (num_classes * counts)
    return torch.tensor(weights, dtype=torch.float32)


def _iterate_batches(n: int, batch_size: int, generator: torch.Generator):
    perm = torch.randperm(n, generator=generator)
    for start in range(0, n, batch_size):
        yield perm[start : start + batch_size].numpy()


def train_model(
    train_x: np.ndarray,
    train_y: np.ndarray,
    val_x: np.ndarray,
    val_y: np.ndarray,
    *,
    seed: int = SEED,
    epochs: int = MAX_EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LEARNING_RATE,
    patience: int = PATIENCE,
) -> tuple[KwsNet, dict]:
    """Train one KwsNet; early-stop on val loss; restore the best weights.

    Single-threaded + fully seeded: identical inputs and seed ⇒ identical
    weights and metrics (NFR-006).
    """
    torch.manual_seed(seed)
    torch.set_num_threads(1)  # thread-count-independent floats (NFR-006)
    generator = torch.Generator(device="cpu").manual_seed(seed)

    model = KwsNet(num_classes=int(train_y.max()) + 1)
    criterion = nn.NLLLoss(weight=_class_weights(train_y, model.num_classes))
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    x_train = torch.from_numpy(train_x)
    y_train = torch.from_numpy(train_y.astype(np.int64))
    x_val = torch.from_numpy(val_x)
    y_val = torch.from_numpy(val_y.astype(np.int64))

    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    best_loss = float("inf")
    best_epoch = 0
    stale = 0
    epochs_run = 0

    for epoch in range(1, epochs + 1):
        epochs_run = epoch
        model.train()
        for idx in _iterate_batches(len(train_y), batch_size, generator):
            optimizer.zero_grad()
            loss = criterion(model.log_probs(x_train[idx]), y_train[idx])
            loss.backward()
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_loss = float(
                criterion(model.log_probs(x_val), y_val)
            )
        if val_loss < best_loss - 1e-12:
            best_loss = val_loss
            best_epoch = epoch
            stale = 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= patience:
                break

    model.load_state_dict(best_state)
    return model, {"best_epoch": best_epoch, "epochs_run": epochs_run, "best_val_loss": best_loss}


# ---------------------------------------------------------------------------
# Scoring + threshold sweep
# ---------------------------------------------------------------------------


def predict_probs(model: KwsNet, x: np.ndarray, batch: int = 256) -> np.ndarray:
    """(N, C) softmax probabilities, eval mode, no grad."""
    model.eval()
    out = []
    with torch.no_grad():
        for start in range(0, len(x), batch):
            out.append(model(torch.from_numpy(x[start : start + batch])).numpy())
    return np.concatenate(out) if out else np.zeros((0, model.num_classes), dtype=np.float32)


def score(model: KwsNet, x: np.ndarray, y: np.ndarray, classes: Sequence[str]) -> dict:
    """Argmax metrics: per-class recall/precision, accuracy, confusion matrix."""
    preds = predict_probs(model, x).argmax(axis=1)
    n_c = len(classes)
    confusion = np.zeros((n_c, n_c), dtype=int)
    for true, pred in zip(y, preds):
        confusion[int(true), int(pred)] += 1
    support = confusion.sum(axis=1)
    recall = {
        c: (confusion[i, i] / support[i]) if support[i] else None
        for i, c in enumerate(classes)
    }
    precision: dict[str, Optional[float]] = {}
    for i, c in enumerate(classes):
        pred_total = confusion[:, i].sum()
        precision[c] = (confusion[i, i] / pred_total) if pred_total else None
    acc = float((preds == y).mean()) if len(y) else None
    return {
        "accuracy": acc,
        "recall_per_class": recall,
        "precision_per_class": precision,
        "support_per_class": {c: int(support[i]) for i, c in enumerate(classes)},
        "confusion": confusion.tolist(),
    }


def decode_with_thresholds(probs: np.ndarray, threshold: float, margin: float) -> np.ndarray:
    """Decode with the runtime rule (FR-015): argmax label accepted only if
    its probability ≥ threshold AND it leads the runner-up by ≥ margin;
    otherwise no detection (label −1)."""
    argmax = probs.argmax(axis=1)
    top1 = probs.max(axis=1)
    runner_up = np.partition(probs, -2, axis=1)[:, -2] if probs.shape[1] > 1 else np.zeros(len(probs))
    accepted = (top1 >= threshold) & ((top1 - runner_up) >= margin)
    return np.where(accepted, argmax, -1)


def sweep_grid(
    probs: np.ndarray,
    y: np.ndarray,
    classes: Sequence[str],
    thresholds: Sequence[float] = THRESHOLD_GRID,
    margins: Sequence[float] = MARGIN_GRID,
) -> list[dict]:
    """Evaluate the threshold × margin grid against scored windows.

    Per point: wake recall, per-command recall (threshold decode, FR-015),
    noise rejection, and min recall — the NFR-003 selection inputs.
    """
    wake_i = classes.index("wake")
    cmd_i = {c: i for i, c in enumerate(classes) if c not in ("wake", "noise")}
    noise_i = classes.index("noise") if "noise" in classes else None

    results = []
    for t in thresholds:
        for m in margins:
            decoded = decode_with_thresholds(probs, t, m)
            wake_mask = y == wake_i
            wake_recall = (
                float(np.mean(decoded[wake_mask] == wake_i)) if wake_mask.any() else None
            )
            command_recalls = {}
            for c, i in cmd_i.items():
                mask = y == i
                command_recalls[c] = (
                    float(np.mean(decoded[mask] == i)) if mask.any() else None
                )
            if noise_i is not None:
                noise_mask = y == noise_i
                # A noise window is "rejected" when nothing decodes OR the
                # model confidently says the null class (noise) — either way
                # no wake/command fires.
                noise_reject = (
                    float(
                        np.mean((decoded[noise_mask] == -1) | (decoded[noise_mask] == noise_i))
                    )
                    if noise_mask.any()
                    else None
                )
            else:
                noise_reject = None
            measured = [r for r in [wake_recall, *command_recalls.values()] if r is not None]
            results.append(
                {
                    "threshold": round(float(t), 2),
                    "margin": round(float(m), 2),
                    "wake_recall": wake_recall,
                    "command_recalls": command_recalls,
                    "noise_rejection": noise_reject,
                    "min_recall": min(measured) if measured else None,
                }
            )
    return results


def choose_operating_point(grid: list[dict], nfr_recall: float = 0.9) -> tuple[dict, bool]:
    """Deterministic operating-point choice (Q-006 "operator-chosen point").

    Prefer every point meeting NFR-003 (wake recall AND every command recall
    ≥ nfr_recall); among those pick the most conservative — max margin, then
    max threshold. If none qualifies, keep the point with the best min recall
    (tie → max margin, then max threshold) and report meets_nfr003=False.
    """
    def key(r: dict) -> tuple:
        rec = [v for v in [r["wake_recall"], *r["command_recalls"].values()] if v is not None]
        min_rec = min(rec) if rec else 0.0
        meets = min_rec >= nfr_recall and len(rec) > 0
        return (meets, round(min_rec, 6), r["margin"], r["threshold"])

    best = max(grid, key=key)
    meets = (
        best["min_recall"] is not None
        and best["min_recall"] >= nfr_recall
    )
    return best, meets


def thresholds_document(classes: Sequence[str], threshold: float, margin: float) -> dict:
    """model/thresholds.json payload (Q-006 schema) — trained commands only.

    Schema (design.md): {"wake", "margin_wake", "commands": {cmd: {threshold,
    margin}}, "window_s"} — one entry per TRAINED command (FR-032), in the
    frozen class order.
    """
    return {
        "wake": round(float(threshold), 2),
        "margin_wake": round(float(margin), 2),
        "commands": {
            c: {"threshold": round(float(threshold), 2), "margin": round(float(margin), 2)}
            for c in classes
            if c not in ("wake", "noise")
        },
        "window_s": WINDOW_S,
    }


# ---------------------------------------------------------------------------
# Orchestration: `ultron train run` / `ultron train sweep`
# ---------------------------------------------------------------------------


def _versions() -> dict:
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "numpy": np.__version__,
    }


def holdout_guard(holdout: dict, classes: Sequence[str]) -> None:
    """Fresh hold-out must cover every trained class (NFR-003 honesty rule)."""
    have = set(holdout["labels"])
    missing = [c for c in classes if c not in have]
    if missing:
        raise TrainError(
            "fresh hold-out is missing trained label(s): "
            f"{', '.join(missing)} — NFR-003 requires honest per-label "
            "hold-out scoring; record hold-out clips for every trained class"
        )


def run_training(
    archive_path: Path | str,
    holdout: dict,
    registry_path: Path | str,
    *,
    seed: int = SEED,
    epochs: int = MAX_EPOCHS,
    batch_size: int = BATCH_SIZE,
    lr: float = LEARNING_RATE,
    patience: int = PATIENCE,
    with_synthetic: bool = False,
    min_originals: Optional[int] = None,
) -> tuple[dict, KwsNet, dict]:
    """Full T8 training pass → (report, trained model, checkpoint payload).

    ``holdout`` is the output of ``ultron.features.extract_holdout`` (already
    restricted to the trained labels by the caller).
    """
    reg = load_registry(registry_path)
    arch = load_archive(archive_path)

    features, labels, paths, syn_used = filter_synthetic(
        arch.features, arch.labels, arch.paths, with_synthetic
    )
    syn_total = int(sum(is_synthetic_path(p) for p in arch.paths))

    classes = derive_class_set(labels, reg)
    counts = originals_clip_counts(labels, paths)
    minimums = check_nfr005(counts, classes, reg, min_originals=min_originals)

    holdout_guard(holdout, classes)

    class_to_idx = {c: i for i, c in enumerate(classes)}
    y = np.array([class_to_idx[str(l)] for l in labels])
    train_idx, val_idx, manifest_hash = clip_split(labels, paths, arch.split_hash, seed=seed)

    model, fit = train_model(
        features[train_idx],
        y[train_idx],
        features[val_idx],
        y[val_idx],
        seed=seed,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        patience=patience,
    )

    val_report = score(model, features[val_idx], y[val_idx], classes)
    hy = np.array([class_to_idx[str(l)] for l in holdout["labels"]])
    holdout_report = score(model, holdout["features"], hy, classes)

    report = {
        "seed": seed,
        "split_hash": arch.split_hash,
        "split_manifest_hash": manifest_hash,
        "class_order": list(classes),
        "hyperparameters": {
            "epochs_max": epochs,
            "batch_size": batch_size,
            "lr": lr,
            "patience": patience,
            "optimizer": "adam",
            "loss": "nll_on_in_graph_softmax (sparse categorical cross-entropy)",
        },
        "fit": fit,
        "dataset_counts": {
            "originals_per_label": counts,
            "windows_per_label": dict(sorted(Counter(str(l) for l in labels).items())),
            "synthetic_rows_in_archive": syn_total,
            "synthetic_rows_used": syn_used,
            "holdout_windows_per_label": dict(sorted(holdout["windows_per_label"].items())),
            "holdout_originals_per_label": dict(sorted(holdout["clips_per_label"].items())),
        },
        "nfr005_minimums": minimums,
        "nfr005_overridden": min_originals is not None,
        # val numbers are memorization context only (NFR-003 honesty rule)
        "val_memorization_context": val_report,
        "holdout_fresh": holdout_report,
        "versions": _versions(),
    }
    checkpoint = {
        "format": 1,
        "model_state": {k: v.detach().clone() for k, v in model.state_dict().items()},
        "classes": list(classes),
        "in_frames": NUM_FRAMES,
        "in_coeffs": NUM_COEFFS,
        "seed": seed,
        "split_hash": arch.split_hash,
        "split_manifest_hash": manifest_hash,
        "torch_version": str(torch.__version__),  # plain str — weights_only-safe
    }
    return report, model, checkpoint


def run_sweep(
    checkpoint: dict,
    holdout: dict,
    *,
    thresholds: Sequence[float] = THRESHOLD_GRID,
    margins: Sequence[float] = MARGIN_GRID,
) -> tuple[dict, dict]:
    """Sweep the grid against the fresh hold-out → (thresholds.json payload,
    sweep report). Uses the trained checkpoint's frozen class order."""
    classes = list(checkpoint["classes"])
    model = KwsNet(num_classes=len(classes))
    model.load_state_dict(checkpoint["model_state"])

    class_to_idx = {c: i for i, c in enumerate(classes)}
    hy = np.array([class_to_idx[str(l)] for l in holdout["labels"]])
    probs = predict_probs(model, holdout["features"])

    grid = sweep_grid(probs, hy, classes, thresholds, margins)
    best, meets = choose_operating_point(grid)
    thresholds_json = thresholds_document(classes, best["threshold"], best["margin"])
    sweep_report = {
        "class_order": classes,
        "grid": grid,
        "chosen": best,
        "meets_nfr003": meets,
        "nfr003_target": 0.9,
        "holdout_windows_per_label": dict(sorted(holdout["windows_per_label"].items())),
        "thresholds_out": thresholds_json,
        "versions": _versions(),
    }
    return thresholds_json, sweep_report


def load_checkpoint(path: Path | str) -> dict:
    path = Path(path)
    if not path.is_file():
        raise TrainError(
            f"checkpoint not found: {path} — run `ultron train run` first (T8)"
        )
    try:
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001
        raise TrainError(f"{path}: cannot load checkpoint ({exc})") from exc
    for key in ("model_state", "classes"):
        if key not in ckpt:
            raise TrainError(f"{path}: checkpoint is missing '{key}' — not a T8 checkpoint")
    return ckpt


def write_json(path: Path | str, payload: dict) -> None:
    """Deterministic JSON (sorted keys, fixed indent) — reruns byte-match."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def write_checkpoint(path: Path | str, checkpoint: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, path)


def write_holdout_report_md(path: Path | str, report: dict) -> None:
    """Human-readable hold-out report (PR evidence, NFR-003 honesty rule)."""
    classes = report["class_order"]
    lines = [
        "# Fresh hold-out report — T8 training run",
        "",
        f"- seed: `{report['seed']}`",
        f"- archive split hash: `{report['split_hash']}`",
        f"- split manifest hash: `{report['split_manifest_hash']}`",
        f"- class order (frozen): `{' '.join(classes)}`",
        f"- torch {report['versions']['torch']} / numpy "
        f"{report['versions']['numpy']} / python {report['versions']['python']}",
        "",
        "## Dataset counts (originals per label, NFR-005)",
        "",
        "| label | originals | minimum | train windows | hold-out windows |",
        "|---|---|---|---|---|",
    ]
    counts = report["dataset_counts"]
    for label in classes:
        lines.append(
            f"| {label} | {counts['originals_per_label'].get(label, 0)} "
            f"| {report['nfr005_minimums'].get(label, '-')} "
            f"| {counts['windows_per_label'].get(label, 0)} "
            f"| {counts['holdout_windows_per_label'].get(label, 0)} |"
        )
    val = report["val_memorization_context"]
    fresh = report["holdout_fresh"]
    lines += [
        "",
        "## Validation split — memorization context only (NFR-003)",
        "",
        f"- accuracy: {val['accuracy']:.4f}" if val["accuracy"] is not None else "- accuracy: n/a",
        "",
        "## Fresh hold-out (never seen in training/augmentation)",
        "",
        f"- accuracy: {fresh['accuracy']:.4f}" if fresh["accuracy"] is not None else "- accuracy: n/a",
        "",
        "| label | recall | precision | support |",
        "|---|---|---|---|",
    ]
    for label in classes:
        rec = fresh["recall_per_class"][label]
        prec = fresh["precision_per_class"][label]
        lines.append(
            f"| {label} | {rec:.4f} | {prec:.4f} | {fresh['support_per_class'][label]} |"
            if rec is not None and prec is not None
            else f"| {label} | n/a | n/a | {fresh['support_per_class'][label]} |"
        )
    lines += ["", "## Confusion matrix (fresh hold-out, rows = true)", ""]
    lines.append("| true \\ pred | " + " | ".join(classes) + " |")
    lines.append("|---" * (len(classes) + 1) + "|")
    for i, label in enumerate(classes):
        lines.append(f"| {label} | " + " | ".join(str(v) for v in fresh["confusion"][i]) + " |")
    lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(Path(path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
