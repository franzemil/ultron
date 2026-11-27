"""``ultron esp32 deploy`` — compile → preflight → flash → (optionally) capture.

Port of the old ``scripts/deploy_xiao.sh``. The rules it encodes are the reason it exists as
one command rather than a README recipe:

1. **compile before touching the port** — a compile error never half-flashes a board;
2. **preflight read** — a port that enumerates but returns zero bytes is a *hung chip*; abort
   (exit 1) instead of uploading into it. A silent chip is a stop condition, never a retry loop;
3. **`arduino-cli upload` has no `--libraries`** — so the build is produced first with
   `--output-dir` and flashed with `upload --input-dir` (no recompile, no library resolution);
4. **per-variant build dir** — a `--selftest` flash can never pick up a production image;
5. exit codes: 0 ok · 1 silent/hung · 2 usage · 3 compile · 4 upload · 5 no console.

Locking is opt-in (``--lock``, off by default): the single XIAO is shared, so hold
``/tmp/ultron-xiao.lock`` whenever another worker may flash at the same time. macOS ``lockf``
takes a command, not a string (no ``-c``), so the lock is taken by re-running this same command
under ``lockf`` with an environment marker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import esp32_console, esp32lib

REPO_ROOT = Path(__file__).resolve().parents[3]
SKETCH = REPO_ROOT / "devices/esp32/ultron_voice"
LIBRARIES = REPO_ROOT / "devices/esp32/libraries"
FQBN = "esp32:esp32:XIAO_ESP32S3"
LOCKFILE = Path("/tmp/ultron-xiao.lock")
LOG_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "ultron-xiao-logs"
LOCK_MARKER = "ULTRON_UNDER_LOCK"

EXIT_OK, EXIT_SILENT, EXIT_USAGE, EXIT_COMPILE, EXIT_UPLOAD, EXIT_NO_CONSOLE = 0, 1, 2, 3, 4, 5


def _say(msg: str) -> None:
    print(f"== {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"!! {msg}", file=sys.stderr, flush=True)


def _fail(msg: str, code: int) -> int:
    print(f"xx {msg}", file=sys.stderr, flush=True)
    return code


@dataclass
class Options:
    selftest: bool = False
    listen: float = 0.0
    port: str = esp32_console.DEFAULT_PORT
    lock: bool = False
    compile_only: bool = False
    skip_preflight: bool = False
    skip_compile: bool = False
    dry_run: bool = False


def build_dir(selftest: bool) -> Path:
    suffix = "-selftest" if selftest else ""
    return LOG_DIR.parent / f"ultron-xiao-build{suffix}"


def compile_command(opts: Options) -> list[str]:
    cmd = [
        "arduino-cli", "compile",
        "--libraries", str(LIBRARIES),
        "--output-dir", str(build_dir(opts.selftest)),
        "--fqbn", FQBN,
    ]
    if opts.selftest:
        cmd.append("--build-property=compiler.cpp.extra_flags=-DULTRON_SELFTEST=1")
    cmd.append(str(SKETCH))
    return cmd


def upload_command(opts: Options) -> list[str]:
    return [
        "arduino-cli", "upload",
        "-p", opts.port,
        "--fqbn", FQBN,
        "--input-dir", str(build_dir(opts.selftest)),
    ]


def _run(cmd: list[str], opts: Options) -> int:
    if opts.dry_run:
        print("   [dry-run] " + " ".join(cmd), flush=True)
        return 0
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode


def _lock_reexec(opts: Options, argv: list[str]) -> int | None:
    """Re-run under lockf once; returns None when already inside (or not asked)."""
    if not opts.lock or os.environ.get(LOCK_MARKER):
        return None
    if not shutil.which("lockf"):
        return _fail("--lock requested but lockf is not on PATH (macOS built-in expected)",
                     EXIT_USAGE)
    _say(f"taking {LOCKFILE}")
    env = dict(os.environ, **{LOCK_MARKER: "1"})
    return subprocess.run(["lockf", "-k", str(LOCKFILE), sys.executable, "-m", "ultron.cli", *argv],
                          cwd=REPO_ROOT, env=env).returncode


def _report_lock_holder(opts: Options) -> None:
    """Without --lock, at least say when another worker owns the board."""
    if opts.lock or opts.dry_run or not shutil.which("lockf"):
        return
    probe = subprocess.run(["lockf", "-k", "-t", "0", str(LOCKFILE), "/usr/bin/true"],
                           capture_output=True)
    if probe.returncode != 0:
        _warn(f"{LOCKFILE} is held by another worker — flashing anyway (no --lock)")
        _warn("if a teammate is mid-upload, stop and re-run with --lock")


def _preflight(opts: Options) -> int:
    if opts.skip_preflight or opts.dry_run:
        _say("preflight: skipped")
        return EXIT_OK
    _say(f"preflight: reading {opts.port} for 5 s")
    rc = esp32_console.main(["--port", opts.port, "--seconds", "5", "--quiet"])
    if rc == 0:
        _say("preflight: chip is talking")
        return EXIT_OK
    if rc == 1:
        return _fail(
            "chip is SILENT (port enumerates, zero bytes). Hung device — needs a physical "
            "reset. Do NOT retry uploads in a loop. Nothing was flashed.",
            EXIT_SILENT,
        )
    return _fail(f"preflight could not read {opts.port} (rc={rc})", EXIT_USAGE)


def _capture(opts: Options) -> int:
    time.sleep(2)  # the chip's USB-Serial/JTAG re-enumerates after the hard reset
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "-selftest" if opts.selftest else ""
    log = LOG_DIR / f"xiao-{datetime.now():%Y%m%d-%H%M%S}{suffix}.log"
    _say(f"capture: {opts.listen:.0f}s → {log}")
    if opts.dry_run:
        print(f"   [dry-run] ultron esp32 console --port {opts.port} --seconds "
              f"{opts.listen:.0f} --raw", flush=True)
        return EXIT_OK
    rc = esp32_console.main(["--port", opts.port, "--seconds", str(opts.listen), "--raw", "--log",
                             str(log)])
    if rc != 0:
        return _fail(
            f"no console output after flashing — check {opts.port}, then run "
            "`uv run ultron esp32 console --seconds 20`",
            EXIT_NO_CONSOLE,
        )
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    evt = [ln for ln in text.splitlines() if ln.startswith("EVT ")]
    preds = [ln for ln in text.splitlines() if ln.startswith("DBG pred=")]
    _say("summary")
    print(f"   EVT lines : {len(evt)}", flush=True)
    print(f"   DBG pred= : {len(preds)}", flush=True)
    print(f"   log       : {log}", flush=True)
    for line in (evt + [ln for ln in text.splitlines() if ln.startswith(("DBG mem", "DBG model"))])[:20]:
        print(f"   {line}", flush=True)
    return EXIT_OK


def deploy(
    *,
    selftest: bool = False,
    listen: float = 0.0,
    port: str = esp32_console.DEFAULT_PORT,
    lock: bool = False,
    compile_only: bool = False,
    skip_preflight: bool = False,
    skip_compile: bool = False,
    dry_run: bool = False,
) -> int:
    """Run the whole sequence; returns the documented exit code."""
    opts = Options(selftest=selftest, listen=listen, port=port, lock=lock,
                   compile_only=compile_only, skip_preflight=skip_preflight,
                   skip_compile=skip_compile, dry_run=dry_run)

    argv = ["esp32", "deploy"]
    if selftest:
        argv.append("--selftest")
    if listen:
        argv += ["--listen", str(listen)]
    if port != esp32_console.DEFAULT_PORT:
        argv += ["--port", port]
    if lock:
        argv.append("--lock")
    if compile_only:
        argv.append("--compile-only")
    if skip_preflight:
        argv.append("--skip-preflight")
    if skip_compile:
        argv.append("--skip-compile")
    if dry_run:
        argv.append("--dry-run")
    locked = _lock_reexec(opts, argv)
    if locked is not None:
        return locked

    if not SKETCH.is_dir():
        return _fail(f"sketch not found: {SKETCH}", EXIT_USAGE)
    if not shutil.which("arduino-cli"):
        return _fail("arduino-cli not on PATH", EXIT_USAGE)
    if not compile_only and not dry_run and not Path(port).exists():
        return _fail(f"port {port} does not exist (board unplugged?)", EXIT_USAGE)

    # artifact gate: a stale library must never be flashed
    if not dry_run:
        ok, problems, notes = esp32lib.check()
        if not ok:
            _warn(f"UltronVoice library artifacts are stale ({', '.join(problems)}) — run:")
            _warn("  uv run ultron export tflite        # or: ultron esp32 config / model")
            return _fail("refusing to build a firmware whose embedded artifacts disagree "
                         "with model/", EXIT_USAGE)

    # 1. compile (never touches the port)
    if skip_compile:
        if not dry_run and not build_dir(selftest).is_dir():
            return _fail(f"--skip-compile needs a previous build in {build_dir(selftest)}",
                         EXIT_USAGE)
        _say("compile: skipped (--skip-compile)")
    else:
        label = " [selftest]" if selftest else ""
        _say(f"compile {FQBN}{label}")
        if not dry_run:
            proc = subprocess.run(compile_command(opts), cwd=REPO_ROOT,
                                  capture_output=True, text=True)
            for line in proc.stdout.splitlines():
                if "Sketch uses" in line or "Global variables" in line:
                    print(line, flush=True)
            if proc.returncode != 0:
                sys.stderr.write(proc.stdout + proc.stderr)
                return _fail("compile failed — nothing was flashed", EXIT_COMPILE)
        else:
            _run(compile_command(opts), opts)
        try:
            print(f"== UltronVoice v{esp32lib.read_version()}", flush=True)
        except esp32lib.Esp32LibError:
            pass

    if compile_only:
        _say("compile-only: done (nothing flashed, port untouched)")
        return EXIT_OK

    _report_lock_holder(opts)

    # 2. preflight: does the chip answer at all?
    rc = _preflight(opts)
    if rc != EXIT_OK:
        return rc

    # 3. upload (pure flash of the compiled artefacts)
    _say(f"flash → {port}")
    rc = _run(upload_command(opts), opts)
    if rc != 0:
        return _fail(
            "upload failed. If esptool says 'No serial data received', the chip is hung: "
            "physical reset required. Do not retry in a loop.",
            EXIT_UPLOAD,
        )

    # 4. optional capture
    if listen > 0:
        return _capture(opts)

    _say("done — flash in place. Capture with:")
    print("   uv run ultron esp32 console --seconds 40", flush=True)
    return EXIT_OK
