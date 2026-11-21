#!/usr/bin/env python3
"""Build the deterministic PLACEHOLDER model used for T12 on-device verification.

Why a placeholder exists (read this before using the numbers):

    The spec-002 trained artifact is **not reproducible on this machine**:
    ``build/features.npz`` does not exist, ``build/checkpoint.pt``'s training run
    saw 6 originals per label (below NFR-005's 30/40 minimums, trained with an
    explicit ``--min-originals`` override) and its fresh hold-out accuracy is
    **0.25** with every sample predicted ``lb``. Shipping that as "the model"
    would be a lie, and *retraining* here would be inventing numbers.

    So T12 verifies the *plumbing* with a placeholder built by the repo's own
    export machinery on a randomly initialized checkpoint — the exact pattern the
    T9 conversion smoke test uses (``tests/test_export_smoke.py``, design.md's
    onnx2tf risk mitigation). The placeholder has the real input signature
    ``[1, 100, 13]`` int8 and the real frozen class order (``wake noise lb la``),
    so the only difference from the trained artifact is the bytes. A placeholder
    proves: the flatbuffer loads, the arena is big enough, inference is fast
    enough, and the event/DBG lines are correct. It does **not** prove
    recognition — it cannot classify "hey ultron" as ``wake``, by construction.

Outputs (all under build/, gitignored):

    build/placeholder/checkpoint_placeholder.pt   T8 checkpoint format
    build/placeholder/features_placeholder.npz    synthetic T7 archive (seed 42)
    build/placeholder/model_int8.tflite           via ultron_model.export.run_export
    build/placeholder/model_fp16.tflite
    build/placeholder/labels.json
    build/placeholder/parity_report.md

Then embed it in the firmware:

    uv run python scripts/gen_esp32_model.py --placeholder

Usage:
    uv sync --extra export               # onnx2tf / tensorflow (heavy, one-off)
    uv run python scripts/gen_esp32_placeholder.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "build/placeholder"
DEFAULT_CONFIG = REPO_ROOT / "devices/esp32/ultron_voice/config.h"

# Deterministic placeholder knobs (recorded in the PR and in model_data.h).
SEED = 42
LOGIT_SCALE = 8.0  # spreads random logits so argmax survives int8 rounding
N_ROWS = 64  # synthetic calibration/parity rows
FEATURE_SEED = 42


class PlaceholderError(RuntimeError):
    """Loud failure — never emit a half-built placeholder artifact set."""


def read_config_classes(config_path: Path) -> list[str]:
    import re

    if not config_path.is_file():
        raise PlaceholderError(
            f"{config_path} not found — run `uv run python scripts/gen_esp32_config.py` first"
        )
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"ULTRON_CLASS_NAMES\[[^\]]*\]\s*=\s*\{([^}]*)\}", text)
    names = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1)) if match else []
    if not names:
        raise PlaceholderError(f"{config_path}: ULTRON_CLASS_NAMES not found")
    return names


def build_checkpoint(classes: list[str], out_path: Path) -> Path:
    """T8-format checkpoint holding a randomly initialized KwsNet."""
    import torch
    from ultron_model.export import reference_model

    model = reference_model(len(classes), seed=SEED, logit_scale=LOGIT_SCALE)
    checkpoint = {
        "format": 1,
        "model_state": model.state_dict(),
        "classes": list(classes),
        "in_frames": 100,
        "in_coeffs": 13,
        "seed": SEED,
        "split_hash": "placeholder-no-split",
        "split_manifest_hash": "placeholder-no-split",
        "torch_version": str(torch.__version__),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, out_path)
    return out_path


def build_archive(classes: list[str], n_rows: int, out_path: Path) -> Path:
    """Synthetic T7-format feature archive (calibration + parity set).

    Random normal features, not audio: nothing here pretends to be dataset rows.
    The archive is only used as the converter's representative dataset and as the
    parity set, both of which only need the right shape/dtype.
    """
    rng = np.random.default_rng(FEATURE_SEED)
    features = rng.standard_normal((n_rows, 100, 13)).astype(np.float32)
    labels = np.array([classes[i % len(classes)] for i in range(n_rows)], dtype=str)
    paths = np.array([f"synthetic/placeholder_{i:04d}.wav" for i in range(n_rows)], dtype=str)
    manifest = "".join(f"{label}\t{path}\t0\n" for label, path in zip(labels, paths))
    split_hash = hashlib.sha256(manifest.encode()).hexdigest()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        np.savez_compressed(
            fh,
            features=features,
            labels=labels,
            paths=paths,
            split_hash=np.array(split_hash),
        )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--rows", type=int, default=N_ROWS)
    args = parser.parse_args(argv)

    try:
        from ultron_model.export import (
            ExportDepsError,
            ExportError,
            format_bytes,
            run_export,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"gen_esp32_placeholder: cannot import the export pipeline ({exc})", file=sys.stderr)
        return 2

    try:
        classes = read_config_classes(args.config)
        args.out.mkdir(parents=True, exist_ok=True)
        checkpoint = build_checkpoint(classes, args.out / "checkpoint_placeholder.pt")
        archive = build_archive(classes, args.rows, args.out / "features_placeholder.npz")
        result = run_export(checkpoint, archive, args.out)
    except PlaceholderError as exc:
        print(f"gen_esp32_placeholder: {exc}", file=sys.stderr)
        return 2
    except ExportDepsError as exc:
        print(
            f"gen_esp32_placeholder: {exc}\n"
            "hint: uv sync --extra export",
            file=sys.stderr,
        )
        return 3
    except (ExportError, Exception) as exc:  # noqa: BLE001
        print(f"gen_esp32_placeholder: export failed: {exc}", file=sys.stderr)
        return 1

    print("gen_esp32_placeholder: PLACEHOLDER model built (random weights, not trained)")
    print(f"  classes           : {' '.join(result.labels)}")
    print(f"  int8 model        : {format_bytes(result.int8_bytes)} (NFR-002 ≤ 100 KB)")
    print(f"  fp16 model        : {format_bytes(result.fp16_bytes)}")
    print(f"  int8↔torch parity : {result.int8_agreement:.4f} over {result.n_parity} rows")
    print(f"  gate (≥ 99 %)     : {'PASS' if result.gate_passed else 'FAIL'}")
    print(f"  artifacts         : {args.out}")
    print(
        "  next              : uv run python scripts/gen_esp32_model.py --placeholder"
    )
    print(
        "  note              : parity here compares torch vs int8 on SYNTHETIC rows — "
        "it validates the conversion chain, not recognition accuracy."
    )
    print(json.dumps({"labels": result.labels, "int8_bytes": result.int8_bytes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
