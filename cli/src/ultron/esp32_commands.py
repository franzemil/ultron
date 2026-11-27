"""``ultron esp32 …`` — XIAO ESP32S3 firmware artifacts and device tooling.

Everything the firmware pipeline needs lives behind this group; there are no helper scripts:

    ultron esp32 config [--check]        thresholds + registry → ultron_config.h
    ultron esp32 model  [--check]        flatbuffer → model_data.h
    ultron esp32 check                   one gate: config + model + vendored mfcc current
    ultron esp32 placeholder             build the deterministic placeholder model
    ultron esp32 console [--seconds N]   read the USB console
    ultron esp32 bench                   say-ultron → hear-Escuchando oracle (Mac speaker)
    ultron esp32 deploy  [--selftest …]  compile → preflight → flash → capture

`ultron export tflite` always refreshes and versions the UltronVoice library, so there is no
separate "refresh the library" command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from . import esp32_config, esp32_console, esp32_model, esp32lib

REPO_ROOT = Path(__file__).resolve().parents[3]

esp32_app = typer.Typer(
    name="esp32",
    help="XIAO ESP32S3 firmware artifacts, console and flashing (spec 003).",
    no_args_is_help=True,
)


@esp32_app.command("config")
def esp32_config_command(
    check: bool = typer.Option(False, "--check", help="Verify only; exit 1 when stale."),
    out: Path = typer.Option(esp32_config.DEFAULT_OUT, "--out", "-o", help="Header to write."),
    thresholds: Path = typer.Option(esp32_config.DEFAULT_THRESHOLDS, "--thresholds"),
    registry: Path = typer.Option(esp32_config.DEFAULT_REGISTRY, "--registry"),
    labels: Optional[Path] = typer.Option(
        None, "--labels", help="Frozen class order (build/labels.json); default: derive."
    ),
) -> None:
    """Generate (or verify) the library's ultron_config.h — thresholds + command gates.

    Nothing about the operating point lives in C sources; this header is the only copy.
    """
    try:
        text = esp32_config.generate(
            out_path=out,
            thresholds_path=thresholds,
            registry_path=registry,
            labels_path=labels if labels is not None else esp32_config.DEFAULT_LABELS,
        )
    except esp32_config.ConfigError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    if check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        if current != text:
            typer.secho(f"{out} is stale — run `uv run ultron esp32 config`", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.secho(f"{out.name} matches model/ (no drift)", fg=typer.colors.GREEN)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.secho(f"wrote {out}", fg=typer.colors.GREEN)


@esp32_app.command("model")
def esp32_model_command(
    check: bool = typer.Option(False, "--check", help="Verify only; exit 1 when stale."),
    model: Optional[Path] = typer.Option(
        None, "--model", "-m",
        help="Flatbuffer (default build/model_int8.tflite, or the placeholder).",
    ),
    placeholder: bool = typer.Option(False, "--placeholder", help="Use the placeholder model."),
    out: Path = typer.Option(esp32_model.DEFAULT_OUT, "--out", "-o", help="Header to write."),
    provenance: Optional[str] = typer.Option(None, "--provenance", help="Boot-banner text."),
) -> None:
    """Embed (or verify) the flatbuffer as the library's model_data.h."""
    model_path = model or (
        esp32_model.DEFAULT_PLACEHOLDER_MODEL if placeholder else esp32_model.DEFAULT_MODEL
    )
    try:
        text = esp32_model.generate(
            model_path,
            out,
            esp32_model.DEFAULT_CONFIG,
            placeholder=placeholder,
            provenance=provenance,
            check=False,
        )
    except esp32_model.ModelDataError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    if check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        if current != text:
            typer.secho(f"{out} is stale — run `uv run ultron esp32 model`", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        typer.secho(f"{out.name} matches {model_path.name} (no drift)", fg=typer.colors.GREEN)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.secho(f"wrote {out} from {model_path}", fg=typer.colors.GREEN)


@esp32_app.command("check")
def esp32_check_command(
    model: Optional[Path] = typer.Option(None, "--model", "-m"),
    placeholder: bool = typer.Option(False, "--placeholder"),
    labels: Optional[Path] = typer.Option(None, "--labels"),
) -> None:
    """One gate: the library matches model/ (config, model bytes, vendored mfcc).

    `ultron esp32 deploy` runs this before compiling and CI should run it — a stale
    library must never reach a board.
    """
    try:
        typer.echo(f"UltronVoice v{esp32lib.read_version()} @ {esp32lib.LIB_DIR}")
        ok, problems, notes = esp32lib.check(model=model, placeholder=placeholder, labels=labels)
    except esp32lib.Esp32LibError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2) from exc

    for note in notes:
        typer.secho(f"note: {note}", fg=typer.colors.YELLOW)
    if not ok:
        typer.secho(
            f"STALE ({', '.join(problems)}) — run `uv run ultron export tflite`, or "
            "`ultron esp32 config` / `ultron esp32 model` for a header-only change",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    typer.secho("library matches model/ (no drift)", fg=typer.colors.GREEN)


@esp32_app.command("placeholder")
def esp32_placeholder_command(
    out: Path = typer.Option(REPO_ROOT / "build/placeholder", "--out", "-o"),
    rows: int = typer.Option(64, "--rows", help="Synthetic calibration rows."),
) -> None:
    """Build the deterministic placeholder model through the repo's export machinery.

    Only needed while spec 002's trained artifact is absent: it proves the plumbing
    (interpreter, arena, latency, EVT lines), never recognition.
    """
    from . import esp32_placeholder

    raise typer.Exit(code=esp32_placeholder.main(["--out", str(out), "--rows", str(rows)]))


@esp32_app.command("console")
def esp32_console_command(
    seconds: float = typer.Option(30.0, "--seconds", "-s", help="How long to listen."),
    port: str = typer.Option(esp32_console.DEFAULT_PORT, "--port", "-p"),
    evt_only: bool = typer.Option(False, "--evt-only", help="Only EVT contract lines."),
    raw: bool = typer.Option(False, "--raw", help="No timestamps (plain log)."),
    quiet: bool = typer.Option(False, "--quiet", help="Health check: exit 1 if zero bytes."),
) -> None:
    """Read the XIAO USB console (stdlib only — no pyserial needed)."""
    args = ["--port", port, "--seconds", str(seconds)]
    if evt_only:
        args.append("--evt-only")
    if raw:
        args.append("--raw")
    if quiet:
        args.append("--quiet")
    raise typer.Exit(code=esp32_console.main(args))


@esp32_app.command("bench")
def esp32_bench_command(
    simulate: bool = typer.Option(False, "--simulate", help="No device: Enter = a wake."),
    seconds: float = typer.Option(0.0, "--seconds", "-s", help="0 = until Ctrl-C."),
    port: str = typer.Option(esp32_console.DEFAULT_PORT, "--port", "-p"),
    voice: str = typer.Option("Mónica", "--voice", help="macOS Spanish voice."),
    text: str = typer.Option("Escuchando", "--text", help="What it says."),
    any_wake: bool = typer.Option(False, "--any-wake", help="Also announce DBG pred=wake."),
    show_pred: bool = typer.Option(False, "--show-pred", help="Echo every DBG pred= line."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print instead of speaking."),
) -> None:
    """Say-ultron → hear-'Escuchando' oracle (host speaker; FR-018 itself is T14)."""
    from . import esp32_bench

    args = ["--port", port, "--seconds", str(seconds), "--voice", voice, "--text", text]
    for flag, on in (("--simulate", simulate), ("--any-wake", any_wake),
                     ("--show-pred", show_pred), ("--dry-run", dry_run)):
        if on:
            args.append(flag)
    raise typer.Exit(code=esp32_bench.main(args))


@esp32_app.command("deploy")
def esp32_deploy_command(
    selftest: bool = typer.Option(False, "--selftest", help="Scripted scores (EVT proof)."),
    listen: float = typer.Option(0.0, "--listen", help="Capture N seconds after flashing."),
    port: str = typer.Option(esp32_console.DEFAULT_PORT, "--port", "-p"),
    lock: bool = typer.Option(False, "--lock", help="Hold /tmp/ultron-xiao.lock for the run."),
    compile_only: bool = typer.Option(False, "--compile-only", help="Build, touch no port."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight"),
    skip_compile: bool = typer.Option(False, "--skip-compile"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands, execute none."),
) -> None:
    """Compile → preflight → flash → (optionally) capture (exit 1–5 as documented)."""
    from . import esp32_deploy

    raise typer.Exit(
        code=esp32_deploy.deploy(
            selftest=selftest,
            listen=listen,
            port=port,
            lock=lock,
            compile_only=compile_only,
            skip_preflight=skip_preflight,
            skip_compile=skip_compile,
            dry_run=dry_run,
        )
    )
