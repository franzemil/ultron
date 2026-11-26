"""``ultron export tflite`` — ONNX → TFLite export + parity gate (T9, FR-010).

Thin CLI wrapper around :func:`ultron_model.export.run_export`. The heavy
conversion stack (torch / onnx2tf / tensorflow) imports lazily inside the
model package; missing dependencies exit cleanly with the
``uv sync --extra export`` hint instead of a traceback.

Exit codes:

- ``0`` — export passed the parity gate
- ``1`` — parity gate FAILED (agreement < 99%, FR-011 / NFR-007)
- ``2`` — bad inputs (missing files, label-order mismatch, bad archive)
- ``3`` — conversion dependencies not installed (requires local run)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from ultron_model.export import (
    ExportDepsError,
    ExportError,
    ParityGateError,
    format_bytes,
    run_export,
)

export_app = typer.Typer(
    name="export",
    help="Model export (T9): ONNX → TFLite + parity gate.",
)


@export_app.command(name="tflite")
def export_tflite_command(
    checkpoint: Path = typer.Option(
        Path("build/checkpoint.pt"),
        "--checkpoint",
        "-c",
        help="Trained checkpoint (T8, build/checkpoint.pt).",
    ),
    features: Path = typer.Option(
        Path("build/features.npz"),
        "--features",
        "-f",
        help="Feature archive (T7) — representative dataset + parity set.",
    ),
    out: Path = typer.Option(
        Path("build"), "--out", "-o", help="Output directory for all artifacts."
    ),
    threshold: float = typer.Option(
        0.99,
        "--threshold",
        "-t",
        min=0.0,
        max=1.0,
        help="Parity gate: PyTorch vs int8 argmax agreement (NFR-007).",
    ),
    max_calibration: int = typer.Option(
        200, "--max-calibration", min=1, help="Representative-dataset sample cap."
    ),
    max_parity: int = typer.Option(
        1000, "--max-parity", min=10, help="Parity-set sample cap (>= 10)."
    ),
    library: bool = typer.Option(
        False,
        "--library",
        help="After exporting, refresh the UltronVoice Arduino library (embed the new "
        "flatbuffer + config, bump library.properties). On by default when --out is build/.",
    ),
    no_library: bool = typer.Option(
        False, "--no-library", help="Never touch devices/esp32/libraries/UltronVoice."
    ),
) -> None:
    """Export the checkpoint to int8 + float16 TFLite; FAIL below 99% parity."""
    try:
        result = run_export(
            checkpoint,
            features,
            out,
            threshold=threshold,
            max_calibration=max_calibration,
            max_parity=max_parity,
        )
    except ParityGateError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        typer.secho(f"parity report: {out / 'parity_report.md'}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from exc
    except ExportDepsError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(code=3) from exc
    except ExportError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    typer.echo(f"labels (frozen order): {', '.join(result.labels)}")
    typer.echo(
        f"model.onnx      {format_bytes(result.onnx_bytes):>10}"
        f"   (intermediate)"
    )
    typer.echo(
        f"model_int8.tflite {format_bytes(result.int8_bytes):>8}"
        f"   XIAO flash (NFR-002 budget <= {format_bytes(100 * 1024)}):"
        f" {'PASS' if result.int8_within_budget else 'OVER BUDGET'}"
    )
    typer.echo(f"model_fp16.tflite {format_bytes(result.fp16_bytes):>8}   Orange Pi")
    typer.echo(
        f"parity: int8 {result.int8_agreement:.2%} (gate >= {result.threshold:.2%} "
        f"{'PASS' if result.gate_passed else 'FAIL'}), "
        f"fp16 {result.fp16_agreement:.2%} over {result.n_parity} samples"
    )
    typer.secho(f"parity report: {result.report_path}", fg=typer.colors.GREEN)

    # Keep the Arduino library in step with what was just exported: the board embeds the
    # flatbuffer + generated thresholds, so a new artifact that never reaches the library
    # means a flashed device that disagrees with the export. `--out build/` (the default)
    # is the real workflow; tests and ad-hoc exports opt in explicitly with --library.
    wants_library = library or (not no_library and out.resolve() == Path("build").resolve())
    if wants_library:
        script = Path(__file__).resolve().parents[3] / "scripts/gen_esp32_library.py"
        if not script.is_file():
            typer.secho(
                f"error: --library requested but {script} is missing (not in the repo?)",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)
        typer.echo("")
        typer.secho("refreshing the UltronVoice Arduino library…", fg=typer.colors.CYAN)
        proc = subprocess.run(
            [sys.executable, str(script), "--model", str(result.int8_path)],
            capture_output=True,
            text=True,
        )
        typer.echo(proc.stdout.rstrip())
        if proc.returncode != 0:
            typer.secho(proc.stderr.rstrip(), fg=typer.colors.RED)
            typer.secho(
                "error: the export succeeded but the library refresh failed — the board "
                "would embed stale artifacts. Fix the library before flashing.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)
