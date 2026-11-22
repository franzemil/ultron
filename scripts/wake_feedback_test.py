#!/usr/bin/env python3
"""wake_feedback_test.py — "say ultron → hear Escuchando" bench harness.

WHAT THIS IS (and is not)
--------------------------
This is a **host-side bench tool**, not the product feature. FR-018/FR-019 ("an accepted
wake plays the fixed `Escuchando` clip through the MAX98357A") is **T14** and lives in the
firmware. This script exists so the wake → spoken-confirmation loop can be *heard and
measured right now*, on this Mac's speaker, driven by the same `EVT WAKE` line the device
prints — i.e. it is the acceptance oracle T14 will be judged against.

It reads the XIAO console, and every time an `EVT WAKE` line appears it plays "Escuchando"
(macOS `say` → cached 16 kHz mono WAV → `afplay`) and prints the host-side announce latency.

TWO WAYS TO USE IT
------------------
    # 1. No device needed: press Enter = "I just said hey ultron"
    python3 scripts/wake_feedback_test.py --simulate

    # 2. Against the board (device must be alive):
    python3 scripts/wake_feedback_test.py --port /dev/cu.usbmodem2101

READ THIS BEFORE BLAMING THE SCRIPT
-----------------------------------
The firmware currently embeds a **placeholder model** (random weights) because spec 002's
trained artifact is not reproducible on this machine. A placeholder cannot classify "hey
ultron" as `wake`, so on the production build you will hear **nothing** no matter how
clearly you speak — that is the model, not the audio path. To exercise the chain on real
hardware, flash the scripted-score build, which emits a real `EVT WAKE` line:

    ./scripts/deploy_xiao.sh --selftest --listen 60

Then this script will speak "Escuchando" on every scripted wake. Once a real
`model_int8.tflite` lands, the same script works unchanged with your voice.

Exit codes: 0 ok | 1 nothing heard from the port | 2 setup problem | 3 voice/audio tooling
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from serial_log import DEFAULT_BAUD, DEFAULT_PORT, open_console  # noqa: E402

CACHE_DIR = Path("/tmp/ultron-wake-feedback")
DEFAULT_TEXT = "Escuchando"
DEFAULT_VOICE = "Mónica"


def clip_duration_s(path: Path) -> float:
    """Duration in seconds (stdlib `wave` — no ffprobe dependency at runtime)."""
    import wave

    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def synth_clip(text: str, voice: str, rate: int, out_wav: Path) -> Path:
    """Render `text` once, silence-trimmed, as 16 kHz / 16-bit / mono WAV.

    Trimmed on purpose: a natural-rate "Escuchando" renders at ~690 ms and FR-018 caps
    the clip at 500 ms, so the same trim + rate the asset needs are applied here — the
    bench clip then sounds like what T14 must ship.
    """
    if out_wav.is_file() and out_wav.stat().st_size > 44:
        return out_wav
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    aiff = out_wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    trim = (
        "silenceremove=start_periods=1:start_threshold=-45dB:"
        "stop_periods=-1:stop_threshold=-45dB,areverse,"
        "silenceremove=start_periods=1:start_threshold=-45dB:"
        "stop_periods=-1:stop_threshold=-45dB,areverse"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(aiff), "-af", trim,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            str(out_wav),
        ],
        check=True,
    )
    aiff.unlink(missing_ok=True)
    return out_wav


class Announcer:
    def __init__(self, clip: Path | None, dry_run: bool, text: str) -> None:
        self.clip = clip
        self.dry_run = dry_run
        self.text = text
        self.count = 0
        self.latencies: list[float] = []

    def announce(self, reason: str, event_ts: float | None = None) -> None:
        """Speak the confirmation; `event_ts` is when the triggering line was read."""
        self.count += 1
        label = f'"{self.text}" ({reason})'
        if self.dry_run or self.clip is None:
            print(f"   [dry-run] would say {label}")
            return
        proc = subprocess.Popen(
            ["afplay", str(self.clip)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if event_ts is not None:
            started_ms = (time.perf_counter() - event_ts) * 1000.0
            self.latencies.append(started_ms)
            print(f"   >> {label} — host latency {started_ms:.0f} ms")
        else:
            print(f"   >> {label}")
        proc.wait()

    def summary(self) -> None:
        print(f"\nwake_feedback: {self.count} announcement(s)")
        if self.latencies:
            print(
                f"  host announce latency: min {min(self.latencies):.0f} ms / "
                f"max {max(self.latencies):.0f} ms "
                "(host tooling only — NFR-010 is measured on the device, T14)"
            )


def simulate(announcer: Announcer, repeat: int, seconds: float) -> int:
    print(
        "simulate mode: press Enter each time you 'say hey ultron' "
        "(Ctrl-C or 'q'+Enter to stop)\n"
    )
    t_end = time.time() + seconds if seconds > 0 else None
    while True:
        if t_end is not None and time.time() >= t_end:
            return 0
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            return 0
        if line.strip().lower() in {"q", "quit", "exit"}:
            return 0
        announcer.announce("simulated EVT WAKE")
        if repeat and announcer.count >= repeat:
            return 0


def watch_device(port: str, baud: int, announcer: Announcer, seconds: float,
                 any_wake: bool, show_pred: bool) -> int:
    try:
        fd = open_console(port, baud)
    except OSError as exc:
        print(f"wake_feedback: cannot open {port}: {exc}", file=sys.stderr)
        return 2

    print(f"listening on {port} @ {baud} — speak \"hey ultron\" (Ctrl-C to stop)\n")
    t_begin = time.time()
    deadline = t_begin + seconds if seconds > 0 else None
    buf = bytearray()
    bytes_seen = 0
    errors = 0
    reopens = 0

    try:
        while deadline is None or time.time() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b""
            except OSError:
                reopens += 1
                if reopens > 20:
                    print("wake_feedback: port keeps failing; giving up", file=sys.stderr)
                    return 2
                time.sleep(0.5)
                try:
                    fd = open_console(port, baud)
                except OSError:
                    pass
                continue
            if not chunk:
                continue
            bytes_seen += len(chunk)
            buf.extend(chunk)
            while b"\n" in buf:
                raw, _, rest = bytes(buf).partition(b"\n")
                buf = bytearray(rest)
                text = raw.decode("utf-8", "replace").strip("\r")
                if not text:
                    continue
                now = time.perf_counter()
                if text.startswith("EVT WAKE") or (any_wake and text.startswith("DBG pred=wake")):
                    conf = ""
                    for token in text.split():
                        if token.startswith("conf="):
                            conf = token.split("=", 1)[1]
                    print(f"WAKE  {text}" + (f"  (conf={conf})" if conf else ""))
                    announcer.announce("EVT WAKE" if text.startswith("EVT") else "DBG pred=wake", now)
                elif text.startswith("EVT ERROR"):
                    errors += 1
                    print(f"ERROR {text}")
                elif text.startswith("DBG boot") or text.startswith("DBG mem") or \
                        text.startswith("DBG model") or text.startswith("DBG ready"):
                    print(f"   {text}")
                elif show_pred and text.startswith("DBG pred="):
                    print(f"   {text}")
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if bytes_seen == 0:
        print(
            "\nwake_feedback: ZERO bytes from the port — the chip is silent (hung device or "
            "board unplugged).\n"
            "  - physical reset needed: unplug USB-C, or BOOT+plug for ROM mode\n"
            "  - meanwhile you can still test the announcement: --simulate",
            file=sys.stderr,
        )
        return 1
    if errors:
        print(f"\nwake_feedback: {errors} EVT ERROR line(s) — see above")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--simulate", action="store_true",
                        help="no device: press Enter to fire a synthetic wake")
    parser.add_argument("--seconds", type=float, default=0,
                        help="stop after N seconds (default: run until Ctrl-C)")
    parser.add_argument("--repeat", type=int, default=0,
                        help="stop after N announcements (0 = unlimited)")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"macOS Spanish voice (default {DEFAULT_VOICE}, es_ES)")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="what the voice says")
    parser.add_argument("--rate", type=int, default=240,
                        help="speech rate for `say` (default 240 wpm — a natural rate renders "
                             "'Escuchando' at ~690 ms, over FR-018's 500 ms cap)")
    parser.add_argument("--any-wake", action="store_true",
                        help="also announce on 'DBG pred=wake' (noisy; for tuning)")
    parser.add_argument("--show-pred", action="store_true",
                        help="also echo every 'DBG pred=' line (what the model actually heard)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print instead of speaking (no audio, no mic)")
    args = parser.parse_args(argv)

    for tool in ("say", "afplay", "ffmpeg"):
        if not args.dry_run and subprocess.run(["which", tool], capture_output=True).returncode:
            print(f"wake_feedback: '{tool}' not found (macOS only)", file=sys.stderr)
            return 3

    clip = None
    if not args.dry_run:
        def slugify(value: str) -> str:
            folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
            return re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-").lower() or "clip"

        slug, voice_slug = slugify(args.text), slugify(args.voice)
        out = CACHE_DIR / f"{slug}-{voice_slug}-r{args.rate}.wav"
        try:
            clip = synth_clip(args.text, args.voice, args.rate, out)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"wake_feedback: cannot render the clip: {exc}", file=sys.stderr)
            return 3
        dur = clip_duration_s(clip)
        print(f'clip: {clip} ("{args.text}", voice {args.voice}, rate {args.rate}, '
              f'16 kHz mono, {dur * 1000:.0f} ms)')
        if dur > 0.500:
            print(
                f'  NOTE: {dur * 1000:.0f} ms exceeds FR-018\'s 500 ms cap for "Escuchando" — '
                'T14 has to trim harder, raise --rate, or pick a shorter phrase.',
                file=sys.stderr,
            )

    announcer = Announcer(clip, args.dry_run, args.text)
    try:
        if args.simulate:
            rc = simulate(announcer, args.repeat, args.seconds)
        else:
            rc = watch_device(args.port, args.baud, announcer, args.seconds,
                              args.any_wake, args.show_pred)
    finally:
        announcer.summary()

    print(
        "\nnote: this is a HOST-side bench harness. FR-018 (the device itself speaking) is T14;"
        "\non a placeholder model no real wake can be recognised — see the docstring."
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
