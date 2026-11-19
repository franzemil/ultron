"""PyTorch training + threshold sweep — T8 (spec 002-can-think).

`ultron train run` / `ultron train sweep` on generated fixtures — never the
real dataset/ (gitignored, absent). Two fixture flavors:

  * a full 4-class DATASET tree (wake/noise/la/lb — TD-012) exercised
    end-to-end through `ultron features` (T7) and the C MFCC binding, for the
    2-epoch smoke run (verify: "2-epoch smoke run on 4-class fixtures");
  * direct feature archives (separable Gaussian rows) for the training-logic
    tests (FR-032/033 guards, NFR-005 counts, synthetic policy, seed
    reproducibility, sweep schema) — audio realism is irrelevant there.
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

import numpy as np
import pytest
import torch
from typer.testing import CliRunner

from ultron.cli import app
from ultron_model.model import KwsNet
from ultron_model import train as t8

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "model" / "registry.json"
SAMPLE_RATE = 16000
NUM_FRAMES = 100
NUM_COEFFS = 13

runner = CliRunner()

# Fixture clip counts meeting NFR-005 as derived from the registry:
# wake ≥ 30, noise ≥ 30, la/lb = 2 variants × 20 = 40 originals each.
N_WAKE, N_NOISE, N_LA, N_LB = 30, 30, 40, 40
CLASS_COUNTS = {"wake": N_WAKE, "noise": N_NOISE, "la": N_LA, "lb": N_LB}


# ---- fixture helpers -------------------------------------------------------


def write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(samples.astype("<i2").tobytes())


def tone(seed: int, seconds: float) -> np.ndarray:
    """Deterministic int16 clip (seeded): two sines + noise, full scale."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    sig = (
        0.4 * np.sin(2 * np.pi * (180 + seed * 37) * t)
        + 0.3 * np.sin(2 * np.pi * (900 + seed * 11) * t)
        + 0.2 * rng.standard_normal(t.size)
    )
    return (np.clip(sig, -1.0, 1.0) * 32767).astype(np.int16)


def build_dataset_tree(root: Path) -> None:
    """Initial 4-class set (wake/noise/la/lb — TD-012) meeting NFR-005."""
    seeds = {"wake": 1, "noise": 2, "la": 3, "lb": 4}
    for label, n in CLASS_COUNTS.items():
        for i in range(n):
            write_wav(root / f"originals/{label}/{label}_xiao_{i:02d}.wav", tone(seeds[label], 0.5))
    # augmented rows join the training set but never count toward minimums
    write_wav(root / "augmented/la/aug_la_xiao_00_gain.wav", tone(3, 0.5))
    write_wav(root / "augmented/lb/aug_lb_xiao_00_gain.wav", tone(4, 0.5))
    # synthetic/ is excluded by `ultron features` unless --with-synthetic
    write_wav(root / "synthetic/la/syn_la_mock_1.0_00.wav", tone(3, 0.5))
    # fresh hold-out (NFR-003): structural isolation by location
    for label in CLASS_COUNTS:
        for i in range(2):
            write_wav(
                root / f"holdout/{label}/{label}_xiao_9{i}.wav", tone(seeds[label], 0.5)
            )


def build_holdout_tree(root: Path) -> None:
    """Hold-out only (for tests that bring their own feature archive)."""
    for label, seed in (("wake", 1), ("noise", 2), ("la", 3), ("lb", 4)):
        for i in range(2):
            write_wav(root / f"holdout/{label}/{label}_xiao_{i:02d}.wav", tone(seed, 0.5))


def gaussian_rows(label: str, n: int, class_index: int, num_classes: int, seed: int):
    """Separable-by-column Gaussian windows for one class."""
    rng = np.random.default_rng(seed)
    mean = np.where(np.arange(NUM_COEFFS) % num_classes == class_index, 1.4, -0.6)
    base = np.tile(mean, (NUM_FRAMES, 1)).astype(np.float32)
    return base + rng.normal(0.0, 0.15, size=(n, NUM_FRAMES, NUM_COEFFS)).astype(np.float32)


def write_archive(
    path: Path,
    counts: dict[str, int],
    synthetic_counts: dict[str, int] | None = None,
) -> str:
    """Direct T7-schema archive from Gaussian features; returns split hash."""
    num_classes = len(counts)
    order = list(counts)
    feats, labels, paths = [], [], []
    for idx, label in enumerate(order):
        rows = gaussian_rows(label, counts[label], idx, num_classes, seed=100 + idx)
        feats.append(rows)
        for i in range(counts[label]):
            labels.append(label)
            paths.append(f"originals/{label}/{label}_xiao_{i:02d}.wav")
    for label, n in (synthetic_counts or {}).items():
        idx = order.index(label)
        rows = gaussian_rows(label, n, idx, num_classes, seed=200 + idx)
        feats.append(rows)
        labels.extend([label] * n)
        paths.extend(f"synthetic/{label}/syn_{label}_mock_1.0_{i:02d}.wav" for i in range(n))

    # split_hash per the T7 manifest rule: "label\tpath\twindow_start\n".
    starts = [0] * len(labels)
    manifest = "".join(
        f"{lab}\t{p}\t{s}\n" for lab, p, s in zip(labels, paths, starts)
    )
    split_hash = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        features=np.concatenate(feats).astype(np.float32),
        labels=np.array(labels, dtype=np.str_),
        paths=np.array(paths, dtype=np.str_),
        split_hash=np.array(split_hash, dtype=np.str_),
    )
    return split_hash


def run_train(archive: Path, dataset_root: Path, *extra: str):
    """`ultron train run` with every output redirected under ``tmp_path``.

    The CLI defaults write ``build/checkpoint.pt`` (+ report, hold-out report)
    into the working tree; a test must never leave artifacts there, so any
    output flag the caller did not pass explicitly is pinned next to the
    archive instead.
    """
    provided = set(extra)
    outputs = [
        ("--out-checkpoint", "checkpoint.pt"),
        ("--out-report", "train_report.json"),
        ("--out-holdout-md", "holdout_report.md"),
    ]
    defaults: list[str] = []
    for flag, name in outputs:
        if flag not in provided:
            defaults += [flag, str(archive.parent / name)]
    return runner.invoke(
        app,
        [
            "train", "run",
            "--archive", str(archive),
            "--holdout", str(dataset_root),
            "--registry", str(REGISTRY),
            *defaults,
            *extra,
        ],
    )


# ---- the model (dynamic C) --------------------------------------------------


def test_model_dynamic_class_count() -> None:
    """C is dynamic (TD-012): 3-class minimum set and the 13-class cap."""
    for num_classes in (3, 13):
        net = KwsNet(num_classes=num_classes)
        out = net(torch.zeros((4, NUM_FRAMES, NUM_COEFFS)))
        assert out.shape == (4, num_classes)
        np.testing.assert_allclose(out.sum(dim=1).detach(), 1.0, rtol=1e-5)


# ---- smoke: 4-class fixtures, end to end (T8 verify) ------------------------


def test_train_smoke_two_epochs_end_to_end(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    build_dataset_tree(dataset)
    archive = tmp_path / "build" / "features.npz"

    feats = runner.invoke(app, ["features", "--dataset", str(dataset), "--out", str(archive)])
    assert feats.exit_code == 0, feats.output

    out_checkpoint = tmp_path / "build" / "checkpoint.pt"
    out_report = tmp_path / "build" / "train_report.json"
    out_holdout_md = tmp_path / "build" / "holdout_report.md"
    result = run_train(
        archive,
        dataset,
        "--epochs", "2",
        "--out-checkpoint", str(out_checkpoint),
        "--out-report", str(out_report),
        "--out-holdout-md", str(out_holdout_md),
    )
    assert result.exit_code == 0, result.output

    assert out_checkpoint.is_file()
    assert out_holdout_md.is_file()
    report = json.loads(out_report.read_text())
    # frozen class order: wake, noise + registry command order filtered (FR-032)
    assert report["class_order"] == ["wake", "noise", "lb", "la"]
    assert report["seed"] == 42
    assert report["split_hash"] != ""
    assert set(report["dataset_counts"]["windows_per_label"]) == set(CLASS_COUNTS)
    # fresh hold-out scored per trained class, separately from val (NFR-003)
    for label in CLASS_COUNTS:
        assert report["holdout_fresh"]["support_per_class"][label] > 0
        assert report["holdout_fresh"]["recall_per_class"][label] is not None
    assert report["holdout_fresh"]["confusion"]  # 4×4 confusion matrix
    assert "torch" in report["versions"]
    assert "# Fresh hold-out report" in out_holdout_md.read_text()

    ckpt = t8.load_checkpoint(out_checkpoint)
    assert ckpt["classes"] == ["wake", "noise", "lb", "la"]
    assert ckpt["split_hash"] == report["split_hash"]


# ---- guards: FR-032/033 + NFR-005 -------------------------------------------


def test_train_missing_noise_fails_naming_label(tmp_path: Path) -> None:
    """FR-033: a set missing `noise` fails while NAMING the label."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, {"wake": N_WAKE, "la": N_LA, "lb": N_LB})

    result = run_train(archive, dataset)
    assert result.exit_code == 1
    assert "missing required label(s): noise" in result.output


def test_train_missing_command_fails(tmp_path: Path) -> None:
    """FR-033: wake + noise alone is below the minimum viable class set."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, {"wake": N_WAKE, "noise": N_NOISE})

    result = run_train(archive, dataset)
    assert result.exit_code == 1
    assert "no command class" in result.output
    assert "la" in result.output  # registered commands are named as candidates


def test_nfr005_count_guard_names_short_label(tmp_path: Path) -> None:
    """NFR-005 per-variant minimums: 10 la originals < 2 variants × 20."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, {"wake": N_WAKE, "noise": N_NOISE, "la": 10, "lb": N_LB})

    result = run_train(archive, dataset)
    assert result.exit_code == 1
    assert "insufficient original recordings" in result.output
    assert "la: have 10, need" in result.output
    assert "40" in result.output  # 2 variants × 20 (registry-derived)


def test_nfr005_override_flag_records_deviation(tmp_path: Path) -> None:
    """--min-originals 0 skips the count guard; the report records it."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, {"wake": N_WAKE, "noise": N_NOISE, "la": 10, "lb": N_LB})
    out_report = tmp_path / "report.json"

    result = run_train(archive, dataset, "--epochs", "2", "--min-originals", "0",
                       "--out-report", str(out_report))
    assert result.exit_code == 0, result.output
    report = json.loads(out_report.read_text())
    assert report["nfr005_overridden"] is True
    assert report["nfr005_minimums"] == {"wake": 0, "noise": 0, "lb": 0, "la": 0}


def test_holdout_must_cover_trained_classes(tmp_path: Path) -> None:
    """NFR-003 honesty: no fresh hold-out for a trained label → loud failure."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    for wav in (dataset / "holdout" / "lb").glob("*.wav"):
        wav.unlink()
    archive = tmp_path / "features.npz"
    write_archive(archive, CLASS_COUNTS)

    result = run_train(archive, dataset)
    assert result.exit_code == 1
    assert "fresh hold-out is missing trained label(s): lb" in result.output


# ---- synthetic policy (spec 001 T5.1) ---------------------------------------


def test_synthetic_rows_only_with_flag(tmp_path: Path) -> None:
    """Synthetic archive rows enter training only via --with-synthetic."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, CLASS_COUNTS, synthetic_counts={"la": 50})
    out_report = tmp_path / "report.json"

    off = run_train(archive, dataset, "--epochs", "2", "--out-report", str(out_report))
    assert off.exit_code == 0, off.output
    report_off = json.loads(out_report.read_text())
    assert report_off["dataset_counts"]["synthetic_rows_in_archive"] == 50
    assert report_off["dataset_counts"]["synthetic_rows_used"] == 0
    assert report_off["dataset_counts"]["windows_per_label"]["la"] == N_LA

    on = run_train(
        archive, dataset, "--epochs", "2", "--out-report", str(out_report),
        "--with-synthetic",
    )
    assert on.exit_code == 0, on.output
    report_on = json.loads(out_report.read_text())
    assert report_on["dataset_counts"]["synthetic_rows_used"] == 50
    assert report_on["dataset_counts"]["windows_per_label"]["la"] == N_LA + 50


# ---- reproducibility (NFR-006) ----------------------------------------------


def test_seed_rerun_same_split_and_metrics(tmp_path: Path) -> None:
    """Same seed + same archive ⇒ same split hash + byte-identical metrics."""
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    split_hash = write_archive(archive, CLASS_COUNTS)

    reports = []
    for run in (1, 2):
        out_report = tmp_path / f"report_{run}.json"
        out_ckpt = tmp_path / f"ckpt_{run}.pt"
        result = run_train(
            archive,
            dataset,
            "--epochs", "6",
            "--out-report", str(out_report),
            "--out-checkpoint", str(out_ckpt),
        )
        assert result.exit_code == 0, result.output
        reports.append(out_report.read_text())

    assert reports[0] == reports[1]  # byte-identical ⇒ same split + metrics
    report = json.loads(reports[0])
    assert report["split_hash"] == split_hash  # the archive's manifest hash
    assert report["split_manifest_hash"]


def test_seed_rerun_same_sweep(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, CLASS_COUNTS)
    ckpt = tmp_path / "build" / "checkpoint.pt"
    trained = run_train(archive, dataset, "--epochs", "4", "--out-checkpoint", str(ckpt))
    assert trained.exit_code == 0, trained.output

    payloads = []
    for run in (1, 2):
        out = tmp_path / f"thresholds_{run}.json"
        sweep_report = tmp_path / f"sweep_{run}.json"
        result = runner.invoke(
            app,
            [
                "train", "sweep",
                "--checkpoint", str(ckpt),
                "--holdout", str(dataset),
                "--out", str(out),
                "--out-report", str(sweep_report),
            ],
        )
        assert result.exit_code == 0, result.output
        payloads.append(sweep_report.read_text())
    assert payloads[0] == payloads[1]


# ---- threshold sweep ---------------------------------------------------------


def test_sweep_writes_thresholds_for_trained_commands_only(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    build_holdout_tree(dataset)
    archive = tmp_path / "features.npz"
    write_archive(archive, CLASS_COUNTS)
    ckpt = tmp_path / "checkpoint.pt"
    trained = run_train(archive, dataset, "--epochs", "4", "--out-checkpoint", str(ckpt))
    assert trained.exit_code == 0, trained.output

    out = tmp_path / "model" / "thresholds.json"
    sweep_report = tmp_path / "sweep.json"
    result = runner.invoke(
        app,
        [
            "train", "sweep",
            "--checkpoint", str(ckpt),
            "--holdout", str(dataset),
            "--out", str(out),
            "--out-report", str(sweep_report),
        ],
    )
    assert result.exit_code == 0, result.output

    doc = json.loads(out.read_text())
    # Q-006 schema, exact keys; trained commands only (FR-032) — no lm/gd/…
    assert set(doc) == {"wake", "margin_wake", "commands", "window_s"}
    assert set(doc["commands"]) == {"lb", "la"}
    assert doc["window_s"] == 5.0
    assert doc["wake"] in t8.THRESHOLD_GRID
    assert doc["margin_wake"] in t8.MARGIN_GRID
    for cmd in doc["commands"].values():
        assert set(cmd) == {"threshold", "margin"}
        assert cmd["threshold"] in t8.THRESHOLD_GRID
        assert cmd["margin"] in t8.MARGIN_GRID

    sweep = json.loads(sweep_report.read_text())
    assert len(sweep["grid"]) == len(t8.THRESHOLD_GRID) * len(t8.MARGIN_GRID)
    assert "meets_nfr003" in sweep and "chosen" in sweep


def test_choose_operating_point_prefers_nfr003_then_margin() -> None:
    """Selection rule: NFR-003-compliant points win; then max margin/threshold."""
    grid = [
        {"threshold": 0.60, "margin": 0.05, "wake_recall": 0.95,
         "command_recalls": {"la": 0.95}, "noise_rejection": None, "min_recall": 0.95},
        {"threshold": 0.60, "margin": 0.20, "wake_recall": 0.95,
         "command_recalls": {"la": 0.95}, "noise_rejection": None, "min_recall": 0.95},
        {"threshold": 0.95, "margin": 0.20, "wake_recall": 0.95,
         "command_recalls": {"la": 0.95}, "noise_rejection": None, "min_recall": 0.95},
        {"threshold": 0.95, "margin": 0.20, "wake_recall": 0.89,  # fails NFR-003
         "command_recalls": {"la": 1.0}, "noise_rejection": None, "min_recall": 0.89},
    ]
    best, meets = t8.choose_operating_point(grid)
    assert meets is True
    assert (best["threshold"], best["margin"]) == (0.95, 0.20)  # most conservative

    fallback, meets_fb = t8.choose_operating_point([grid[3]])
    assert meets_fb is False
    assert fallback is grid[3]


def test_decode_with_thresholds_margin_rule() -> None:
    """FR-015 decode: below-threshold or thin-margin scores detect nothing."""
    probs = np.array(
        [
            [0.85, 0.10],  # clear win → detected
            [0.84, 0.75],  # thin margin (0.09 < 0.10) → rejected
            [0.59, 0.30],  # below threshold 0.60 → rejected
        ]
    )
    decoded = t8.decode_with_thresholds(probs, threshold=0.60, margin=0.10)
    assert decoded.tolist() == [0, -1, -1]


# ---- archive validation ------------------------------------------------------


def test_train_rejects_corrupt_archive(tmp_path: Path) -> None:
    """NaN rows / wrong shape fail loudly — no silent training (FR-022)."""
    bad = tmp_path / "bad.npz"
    feats = np.zeros((3, NUM_FRAMES, NUM_COEFFS), dtype=np.float32)
    feats[1, 0, 0] = float("nan")
    np.savez(
        bad,
        features=feats,
        labels=np.array(["wake", "noise", "la"], dtype=np.str_),
        paths=np.array(["a.wav", "b.wav", "c.wav"], dtype=np.str_),
        split_hash=np.array("x" * 64, dtype=np.str_),
    )
    with pytest.raises(t8.TrainError, match="NaN"):
        t8.load_archive(bad)

    wrong = tmp_path / "wrong.npz"
    np.savez(
        wrong,
        features=np.zeros((3, 10, NUM_COEFFS), dtype=np.float32),
        labels=np.array(["wake", "noise", "la"], dtype=np.str_),
        paths=np.array(["a.wav", "b.wav", "c.wav"], dtype=np.str_),
        split_hash=np.array("x" * 64, dtype=np.str_),
    )
    with pytest.raises(t8.TrainError, match=r"\(N, 100, 13\)"):
        t8.load_archive(wrong)


def test_pool_stats_is_int8_safe():
    """Centred mean + MAD spread: no cancellation, no sqrt, large offsets ignored.

    `E[x²] − E[x]²` cancels catastrophically under int8 quantization, and `sqrt`
    amplifies near-zero error — both broke the export parity gate. The spread is
    therefore mean absolute deviation of the centred activations.
    """
    import torch
    from ultron_model.model import pool_stats

    torch.manual_seed(0)
    z = torch.rand(8, 5, 40) + 100.0  # large common offset, small real spread
    mean, spread = pool_stats(z)

    assert torch.allclose(mean, z.mean(dim=-1), atol=1e-4)
    assert torch.allclose(spread, (z - z.mean(dim=-1, keepdim=True)).abs().mean(dim=-1), atol=1e-5)
    assert float(spread.max()) < 1.0  # the offset did not leak into the spread
    assert float(spread.min()) >= 0.0

    # a constant signal has exactly zero spread
    const = torch.full((2, 4, 10), 7.0)
    _, spread_c = pool_stats(const)
    assert torch.allclose(spread_c, torch.zeros_like(spread_c), atol=1e-6)
