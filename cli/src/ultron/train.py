"""``ultron train`` — PyTorch training + threshold sweep (T8, spec 002).

``ultron train run``    train from build/features.npz (T7) → checkpoint +
                        deterministic report + fresh hold-out report (NFR-003)
``ultron train sweep``  grid threshold × margin against the fresh hold-out →
                        model/thresholds.json (Q-006; FR-015 per-class
                        threshold + margin, trained commands only — FR-032)

The heavy imports (torch, ultron_model) live inside the command bodies so
every other ``ultron`` subcommand stays torch-free at import time.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ultron.features import FeaturesError, extract_holdout

train_app = typer.Typer(
    name="train",
    help="PyTorch training + threshold sweep (T8).",
)


def _fail(exc: Exception) -> None:
    typer.secho(f"error: {exc}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


def _extract_holdout(dataset_root: Path, labels: set[str]) -> dict:
    """Extract fresh hold-out windows through the shared C MFCC path."""
    try:
        return extract_holdout(dataset_root=dataset_root, labels=labels)
    except FeaturesError as exc:
        _fail(exc)
        raise  # unreachable — _fail exits


@train_app.command(name="run")
def train_run(
    archive: Path = typer.Option(
        "build/features.npz", "--archive", "-a", help="T7 feature archive."
    ),
    holdout: Path = typer.Option(
        "dataset",
        "--holdout",
        help="Dataset root containing holdout/<label>/ (fresh hold-out).",
    ),
    registry: Path = typer.Option(
        "model/registry.json", "--registry", help="Command registry (D-005)."
    ),
    out_checkpoint: Path = typer.Option(
        "build/checkpoint.pt", "--out-checkpoint", help="Trained checkpoint (T9 input)."
    ),
    out_report: Path = typer.Option(
        "build/train_report.json", "--out-report", help="Deterministic training report."
    ),
    out_holdout_md: Path = typer.Option(
        "build/holdout_report.md", "--out-holdout-md", help="Human-readable hold-out report."
    ),
    seed: int = typer.Option(42, "--seed", help="RNG seed (NFR-006: default 42)."),
    epochs: int = typer.Option(300, "--epochs", help="Max epochs (design: 300)."),
    batch_size: int = typer.Option(16, "--batch-size", help="Batch size (design: 16)."),
    lr: float = typer.Option(1e-3, "--lr", help="Adam learning rate (design: 1e-3)."),
    patience: int = typer.Option(30, "--patience", help="Early-stop patience (design: 30)."),
    with_synthetic: bool = typer.Option(
        False,
        "--with-synthetic",
        help="Include synthetic-tagged archive rows (default off — owner decides "
        "per run; every use is a changelog event, spec 001 T5.1).",
    ),
    min_originals: int = typer.Option(
        -1,
        "--min-originals",
        help="Override the NFR-005 per-label originals minimum with this value "
        "(0 skips the check). Default -1 = registry-derived minimums with a "
        "hard fail naming short labels. Deviation is recorded in the report.",
    ),
) -> None:
    """Train the KWS model from the T7 archive + score the fresh hold-out.

    Class set is derived from the archive labels (FR-032) — requires `wake`,
    `noise` and ≥ 1 command, failing with the missing labels named (FR-033).
    The fresh hold-out is scored after training and reported separately
    (NFR-003); val-split numbers are memorization context only.
    """
    from ultron_model import train as t8

    try:
        # Two-pass: derive the class set first so the hold-out extraction is
        # restricted to the trained subset (FR-032 — untrained labels can't
        # pollute the fresh hold-out report).
        reg = t8.load_registry(registry)
        arch = t8.load_archive(archive)
        _features, labels, _paths, _used = t8.filter_synthetic(
            arch.features, arch.labels, arch.paths, with_synthetic
        )
        classes = t8.derive_class_set(labels, reg)
        holdout = _extract_holdout(holdout, set(classes))

        report, _model, checkpoint = t8.run_training(
            archive,
            holdout,
            registry,
            seed=seed,
            epochs=epochs,
            batch_size=batch_size,
            lr=lr,
            patience=patience,
            with_synthetic=with_synthetic,
            min_originals=None if min_originals < 0 else min_originals,
        )
        t8.write_checkpoint(out_checkpoint, checkpoint)
        t8.write_json(out_report, report)
        t8.write_holdout_report_md(out_holdout_md, report)
    except (t8.TrainError, FeaturesError) as exc:
        _fail(exc)

    counts = report["dataset_counts"]
    typer.echo(
        f"train: {len(labels)} windows, classes [{' '.join(classes)}] "
        f"(seed {report['seed']}, best epoch {report['fit']['best_epoch']}/"
        f"{report['fit']['epochs_run']})"
    )
    typer.echo(f"  originals: {json.dumps(counts['originals_per_label'])}")
    typer.echo(
        "  val (memorization context): "
        f"acc {report['val_memorization_context']['accuracy']:.4f}"
    )
    fresh = report["holdout_fresh"]
    typer.echo(f"  fresh hold-out: acc {fresh['accuracy']:.4f}")
    for label in classes:
        typer.echo(
            f"    {label}: recall {fresh['recall_per_class'][label]:.4f} "
            f"({fresh['support_per_class'][label]} windows)"
        )
    typer.echo(f"  split hash:        {report['split_hash']}")
    typer.echo(f"  split manifest:    {report['split_manifest_hash']}")
    typer.echo(f"  checkpoint:        {out_checkpoint}")
    typer.echo(f"  report:            {out_report}")
    typer.echo(f"  hold-out report:   {out_holdout_md}")
    typer.echo(
        f"  versions: torch {report['versions']['torch']}, "
        f"numpy {report['versions']['numpy']}, "
        f"python {report['versions']['python']}"
    )


@train_app.command(name="sweep")
def train_sweep(
    checkpoint: Path = typer.Option(
        "build/checkpoint.pt", "--checkpoint", "-c", help="T8 checkpoint (from train run)."
    ),
    holdout: Path = typer.Option(
        "dataset",
        "--holdout",
        help="Dataset root containing holdout/<label>/ (fresh hold-out).",
    ),
    out: Path = typer.Option(
        "model/thresholds.json", "--out", "-o", help="Operating point output (Q-006)."
    ),
    out_report: Path = typer.Option(
        "build/sweep_report.json", "--out-report", help="Full grid report."
    ),
) -> None:
    """Sweep threshold × margin against the fresh hold-out → thresholds.json.

    Grid (design.md): threshold 0.60–0.95 × margin 0.05–0.20. The chosen
    operating point prefers NFR-003 compliance (wake + every trained command
    ≥ 90% recall) with the most conservative margin, then threshold; entries
    are written for TRAINED commands only (FR-032). Threshold changes are
    PR-documented events (Q-006).
    """
    from ultron_model import train as t8

    try:
        ckpt = t8.load_checkpoint(checkpoint)
        holdout = _extract_holdout(holdout, set(ckpt["classes"]))
        thresholds, sweep_report = t8.run_sweep(ckpt, holdout)
        t8.write_json(out, thresholds)
        t8.write_json(out_report, sweep_report)
    except (t8.TrainError, FeaturesError) as exc:
        _fail(exc)

    chosen = sweep_report["chosen"]
    status = "meets NFR-003 (≥ 0.90 wake + command recall)" if sweep_report["meets_nfr003"] \
        else "WARNING: no grid point meets NFR-003 — best min-recall point written"
    typer.echo(
        f"sweep: threshold {chosen['threshold']:.2f} × margin {chosen['margin']:.2f} "
        f"— {status}"
    )
    typer.echo(f"  wake recall: {chosen['wake_recall']:.4f}")
    for label, recall in chosen["command_recalls"].items():
        typer.echo(f"  {label} recall: {recall:.4f}")
    if chosen["noise_rejection"] is not None:
        typer.echo(f"  noise rejection: {chosen['noise_rejection']:.4f}")
    typer.echo(f"  thresholds: {out}")
    typer.echo(f"  report:     {out_report}")
