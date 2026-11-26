#!/usr/bin/env python3
"""Read the XIAO's USB console and print one timestamped line at a time.

Standard library only (``termios``): pyserial is not installed on this machine and
the console read must work from a bare checkout.

The XIAO ESP32S3 exposes its console over the native USB-Serial/JTAG peripheral —
the same port used to flash. Take the shared device lock before reading (macOS has
no ``flock``; ``lockf`` has no ``-c``, so wrap a shell):

    lockf -k /tmp/ultron-xiao.lock /bin/sh -c \
      'uv run ultron esp32 console --port /dev/cu.usbmodem2101 --seconds 40'

A silent port is a hardware fault (hung chip), not an empty room: this script says so
explicitly and exits non-zero, so callers never mistake "no bytes" for "no events".

Usage:
    uv run ultron esp32 console                     # 30 s, EVT+DBG, all lines
    uv run ultron esp32 console --seconds 15 --evt-only
    uv run ultron esp32 console --raw               # no timestamps, for > file.log
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import termios
from pathlib import Path
import time

DEFAULT_PORT = "/dev/cu.usbmodem2101"
DEFAULT_BAUD = 115200


def open_console(port: str, baud: int) -> int:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    # iflag, oflag, cflag, lflag: raw 8N1, no flow control, no echo
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CREAD | termios.CLOCAL | termios.CS8
    attrs[3] = 0
    speed = getattr(termios, f"B{baud}")
    attrs[4] = speed
    attrs[5] = speed
    # VMIN=0 / VTIME=1 → read() returns after ~100 ms with b"" instead of blocking
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--seconds", type=float, default=30.0, help="how long to listen")
    parser.add_argument(
        "--evt-only",
        action="store_true",
        help="print only EVT contract lines (what hosts/bench parse)",
    )
    parser.add_argument("--raw", action="store_true", help="no timestamps (plain log file)")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing but exit code (health check: did the chip say anything?)",
    )
    parser.add_argument(
        "--log", type=Path, default=None, help="also write the raw lines to this file"
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.port):
        print(f"esp32 console: {args.port} does not exist (board unplugged?)", file=sys.stderr)
        return 2
    try:
        fd = open_console(args.port, args.baud)
    except OSError as exc:
        print(f"esp32 console: cannot open {args.port}: {exc}", file=sys.stderr)
        return 2

    t0 = time.time()
    deadline = t0 + args.seconds
    buf = bytearray()
    bytes_seen = 0
    lines = 0
    evt_lines = 0
    collected: list[str] = []
    reopens = 0

    try:
        while time.time() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                chunk = b""
            except OSError as exc:
                # The sketch's USB CDC — or the whole USB-Serial-JTAG peripheral —
                # disappears for a moment when the chip reboots or the sketch
                # re-initialises USB (e.g. right after flashing). Re-open and keep
                # listening instead of pretending the run ended.
                reopens += 1
                if reopens > 20:
                    print(f"serial_log: port keeps failing ({exc}); giving up", file=sys.stderr)
                    return 2
                try:
                    os.close(fd)
                except OSError:
                    pass
                time.sleep(0.5)
                try:
                    fd = open_console(args.port, args.baud)
                except OSError:
                    pass
                continue
            if not chunk:
                continue
            bytes_seen += len(chunk)
            buf.extend(chunk)
            while b"\n" in buf:
                raw_line, _, rest = bytes(buf).partition(b"\n")
                buf = bytearray(rest)
                text = raw_line.decode("utf-8", "replace").strip("\r")
                if not text:
                    continue
                lines += 1
                is_evt = text.startswith("EVT ")
                if is_evt:
                    evt_lines += 1
                if args.evt_only and not is_evt:
                    continue
                if args.quiet:
                    continue
                collected.append(text)
                if args.raw:
                    print(text, flush=True)
                else:
                    stamp = dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    print(f"[{stamp} +{time.time() - t0:6.2f}s] {text}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)

    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text("\n".join(collected) + ("\n" if collected else ""), encoding="utf-8")
    if not args.quiet:
        print(
            f"esp32 console: {lines} line(s), {evt_lines} EVT, {bytes_seen} byte(s) in "
            f"{time.time() - t0:.1f}s",
            file=sys.stderr,
        )
    if bytes_seen == 0:
        print(
            "esp32 console: ZERO bytes — the port enumerated but the chip said nothing. "
            "That is a hung/absent device (needs a physical reset), not an empty log; "
            "do NOT retry uploads in a loop.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
