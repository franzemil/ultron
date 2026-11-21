#!/usr/bin/env python3
"""Pull WAVs off the XIAO ``wav_recorder`` over USB serial — no card reader needed.

Why this exists: the recorder writes to the microSD card, but the card is inside
the XIAO Sense expansion and the recording pass is usually driven from a host
(``devices/esp32/wav_recorder/wav_recorder.ino``). macOS has no scriptable
port-power control and an agent cannot press the board's buttons, so the
recorder itself serves the files over the same USB port with a raw ``get``
dump; this script drives it.

Serial protocol implemented by the sketch (see ``docs/recording.md``)::

    rec <label>   →  OK /sdcard/<label>_xiao_<nn>.wav
                      DONE <bytes>
                      DBG peak=<n> mean=<n> samples=<n>
    ls            →  LIST <name> <bytes>  …  LISTDONE <count>
    get <name>    →  BEGIN <name> <bytes>\\n<raw bytes>\\nEND\\n

Usage::

    # what is on the card?
    python3 devices/esp32/tools/xiao_pull.py ls

    # record one 5 s take per trained label (3 takes each) and pull them
    python3 devices/esp32/tools/xiao_pull.py session \\
        --labels wake,noise,la,lb --takes 3 --out /tmp/xiao_takes

    # pull everything that is already on the card
    python3 devices/esp32/tools/xiao_pull.py pull --out /tmp/xiao_takes

    # record a single take and pull it
    python3 devices/esp32/tools/xiao_pull.py rec la --out /tmp/xiao_takes/la

Only the Python standard library is used (no pyserial): the S3's USB-Serial-JTAG
CDC ignores the line rate, so a raw termios handle is enough, and asserting
DTR/RTS is what makes the Arduino ``HWCDC`` console send.

The recordings are **never** committed (``dataset/`` is gitignored); ingest them
with ``ultron dataset ingest <dir> <label> --mic xiao --dest dataset/originals``.
"""

from __future__ import annotations

import argparse
import os
import select
import struct
import sys
import time
import wave

DEFAULT_PORT = "/dev/cu.usbmodem2101"
DEFAULT_BAUD = 115200
EXPECTED_RATE = 16000
EXPECTED_WIDTH = 2
EXPECTED_CHANNELS = 1


class RecorderError(RuntimeError):
    """Raised when the recorder reports an error or stops answering."""


class XaioRecorder:
    """Minimal raw-serial client for the XIAO wav_recorder sketch."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, open_timeout: float = 20.0):
        self.port = port
        self.baud = baud
        self.open_timeout = open_timeout
        self.fd: int | None = None
        self._buf = bytearray()

    # ---- lifecycle -------------------------------------------------------
    def __enter__(self) -> "XaioRecorder":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def open(self) -> None:
        import termios
        import fcntl

        deadline = time.time() + self.open_timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                self.fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
                break
            except OSError as exc:  # port not (re)enumerated yet
                last = exc
                time.sleep(0.25)
        if self.fd is None:
            raise RecorderError(
                f"cannot open {self.port}: {last}. Is the XIAO plugged in, and did you "
                "flash devices/esp32/wav_recorder?"
            )

        attrs = termios.tcgetattr(self.fd)
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = attrs
        iflag &= ~(
            termios.IGNBRK
            | termios.BRKINT
            | termios.PARMRK
            | termios.ISTRIP
            | termios.INLCR
            | termios.IGNCR
            | termios.ICRNL
            | termios.IXON
            | termios.IXOFF
        )
        oflag &= ~termios.OPOST
        lflag &= ~(
            termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN
        )
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        speed = getattr(termios, f"B{self.baud}")
        cc = list(cc)
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(
            self.fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc]
        )
        # The ESP32-S3 USB-Serial-JTAG CDC only transmits when the host has
        # asserted the modem control lines (this is what HWCDC checks).
        bits = struct.unpack("I", fcntl.ioctl(self.fd, termios.TIOCMGET, b"\0\0\0\0"))[0]
        bits |= termios.TIOCM_DTR | termios.TIOCM_RTS
        fcntl.ioctl(self.fd, termios.TIOCMSET, struct.pack("I", bits))

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None

    # ---- low-level I/O ---------------------------------------------------
    def _fill(self, timeout: float) -> bool:
        """Wait up to <timeout> for more bytes; append them to the buffer."""
        assert self.fd is not None
        ready, _, _ = select.select([self.fd], [], [], max(timeout, 0.0))
        if not ready:
            return False
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            return False
        if not chunk:
            return False
        self._buf.extend(chunk)
        return True

    def read_line(self, timeout: float = 10.0) -> str:
        """Read one \\r\\n-terminated line (control channel)."""
        deadline = time.time() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                return line.decode("utf-8", "replace").rstrip("\r")
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RecorderError(
                    f"timeout waiting for a line from {self.port} "
                    f"({len(self._buf)} byte(s) buffered)"
                )
            self._fill(min(remaining, 0.5))

    def read_exact(self, size: int, timeout: float = 30.0) -> bytes:
        """Read exactly <size> raw bytes (file dump channel)."""
        deadline = time.time() + timeout
        out = bytearray()
        if self._buf:
            take = min(size, len(self._buf))
            out += self._buf[:take]
            del self._buf[:take]
        while len(out) < size:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RecorderError(f"timeout reading {size} bytes ({len(out)} received)")
            if self._fill(min(remaining, 1.0)) and self._buf:
                take = min(size - len(out), len(self._buf))
                out += self._buf[:take]
                del self._buf[:take]
        return bytes(out)

    def send(self, line: str) -> None:
        assert self.fd is not None
        os.write(self.fd, (line + "\r\n").encode())

    def drain(self, seconds: float = 0.3) -> None:
        """Discard anything already buffered/in flight (boot banner, leftovers)."""
        assert self.fd is not None
        end = time.time() + seconds
        while time.time() < end:
            self._fill(0.1)
        self._buf.clear()

    # ---- commands --------------------------------------------------------
    def hello(self, timeout: float = 5.0) -> str:
        """Wait for the recorder banner (proves the sketch is alive)."""
        self.send("help")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self.read_line(timeout=max(0.1, deadline - time.time()))
            except RecorderError:
                break
            if line.startswith("commands:"):
                return line
        raise RecorderError(
            f"no answer from the recorder on {self.port}. The board may be hung "
            "(press RESET / replug USB-C) or the recorder sketch is not flashed."
        )

    def list_files(self) -> list[tuple[str, int]]:
        self.send("ls")
        files: list[tuple[str, int]] = []
        deadline = time.time() + 20.0
        while time.time() < deadline:
            line = self.read_line(timeout=max(0.1, deadline - time.time()))
            if line.startswith("LISTDONE"):
                return files
            if line.startswith("LIST "):
                _, name, size = line.split(" ", 2)
                files.append((name, int(size)))
            elif line.startswith("ERR"):
                raise RecorderError(f"recorder: {line}")
        raise RecorderError("timeout waiting for LISTDONE")

    def get_file(self, name: str) -> bytes:
        self.send(f"get {name}")
        line = self.read_line(timeout=20.0)
        if line.startswith("ERR"):
            raise RecorderError(f"recorder: {line}")
        if not line.startswith("BEGIN "):
            raise RecorderError(f"unexpected reply to `get {name}`: {line!r}")
        _, got_name, size_s = line.split(" ", 2)
        size = int(size_s)
        payload = self.read_exact(size, timeout=60.0 + size / 5000.0)
        tail = ""  # the sketch sends "\nEND\n": skip the empty line closing the dump
        for _ in range(3):
            if tail:
                break
            tail = self.read_line(timeout=10.0).strip()
        if tail != "END":
            raise RecorderError(f"bad dump framing after {got_name}: {tail!r}")
        return payload

    def record(self, label: str, seconds: float = 8.0) -> tuple[str, int]:
        """Run one `rec <label>` take; return (serial path, byte count)."""
        self.send(f"rec {label}")
        line = self.read_line(timeout=20.0)
        if line.startswith("ERR"):
            raise RecorderError(f"recorder: {line}")
        if not line.startswith("OK "):
            raise RecorderError(f"unexpected reply to `rec {label}`: {line!r}")
        path = line[3:].strip()
        deadline = time.time() + seconds
        while time.time() < deadline:
            line = self.read_line(timeout=max(0.1, deadline - time.time()))
            if line.startswith("DONE "):
                return path, int(line.split(" ", 1)[1])
            if line.startswith("ERR"):
                raise RecorderError(f"recorder: {line}")
            if line.startswith("DBG "):
                print(f"        {line}")
        raise RecorderError(f"timeout waiting for DONE after `rec {label}`")


# ---- WAV helpers ---------------------------------------------------------
def wav_duration(data: bytes) -> tuple[int, int, int, float]:
    """Return (rate, sampwidth, channels, seconds) for a WAV payload."""
    if len(data) < 44:
        raise RecorderError(f"clip is too short to be a WAV ({len(data)} bytes)")
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise RecorderError("clip is not a RIFF/WAVE file")
    rate = sampwidth = None
    channels = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (chunk_size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        body = pos + 8
        if chunk_id == b"fmt ":
            _fmt, channels, rate = struct.unpack("<HHI", data[body : body + 8])
            (bits,) = struct.unpack("<H", data[body + 14 : body + 16])
            sampwidth = bits // 8  # container width in bytes, like wave.getsampwidth()
        elif chunk_id == b"data":
            if not rate or not sampwidth or not channels:
                raise RecorderError("data chunk before fmt chunk")
            seconds = chunk_size / float(rate * sampwidth * channels)
            return rate, sampwidth, channels, seconds
        pos = body + chunk_size + (chunk_size & 1)
    raise RecorderError("no data chunk found")


def save_wav(out_dir: str, name: str, payload: bytes) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, os.path.basename(name))
    with open(path, "wb") as fh:
        fh.write(payload)
    rate, width, chans, seconds = wav_duration(payload)
    ok = (rate, width, chans) == (EXPECTED_RATE, EXPECTED_WIDTH, EXPECTED_CHANNELS)
    flag = "ok" if ok else "UNEXPECTED FORMAT"
    print(
        f"  {path}  {len(payload)} B  {rate} Hz/{width * 8}-bit/{chans}ch  "
        f"{seconds:.2f} s  [{flag}]"
    )
    if not ok:
        raise RecorderError(
            f"{path}: expected {EXPECTED_RATE} Hz / 16-bit / mono, got "
            f"{rate} Hz / {width * 8}-bit / {chans}ch"
        )
    with wave.open(path, "rb") as wh:  # extra guard: the stdlib must parse it too
        assert wh.getframerate() == EXPECTED_RATE
    return path


# ---- subcommands ---------------------------------------------------------
def cmd_ls(rec: XaioRecorder, args: argparse.Namespace) -> int:
    files = rec.list_files()
    if not files:
        print("(card is empty)")
        return 0
    total = 0
    for name, size in files:
        print(f"{size:>9}  {name}")
        total += size
    print(f"{total:>9}  ({len(files)} file(s))")
    return 0


def cmd_pull(rec: XaioRecorder, args: argparse.Namespace) -> int:
    files = rec.list_files()
    wavs = [(n, s) for n, s in files if n.lower().endswith(".wav")]
    if args.prefix:
        wavs = [w for w in wavs if w[0].startswith(args.prefix)]
    if not wavs:
        print("no matching .wav files on the card")
        return 1
    print(f"pulling {len(wavs)} wav(s) → {args.out}")
    for name, _size in wavs:
        save_wav(args.out, name, rec.get_file(name))
    return 0


def cmd_rec(rec: XaioRecorder, args: argparse.Namespace) -> int:
    for i in range(args.takes):
        print(f"take {i + 1}/{args.takes} — say `{args.label}` now …", flush=True)
        time.sleep(args.delay)
        path, size = rec.record(args.label, seconds=args.seconds + 10.0)
        print(f"  recorded {path} ({size} B)")
        payload = rec.get_file(os.path.basename(path))
        save_wav(args.out or ".", os.path.basename(path), payload)
        if i + 1 < args.takes:
            time.sleep(args.gap)
    return 0


def cmd_session(rec: XaioRecorder, args: argparse.Namespace) -> int:
    labels = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]
    if not labels:
        raise RecorderError("--labels is empty")
    print(f"session: {len(labels)} label(s) × {args.takes} take(s) → {args.out}")
    failures = 0
    for label in labels:
        out_dir = os.path.join(args.out, label)
        for i in range(args.takes):
            print(f"\n[{label} {i + 1}/{args.takes}] say `{label}` now …", flush=True)
            time.sleep(args.delay)
            try:
                path, size = rec.record(label, seconds=args.seconds + 10.0)
                print(f"  recorded {path} ({size} B)")
                payload = rec.get_file(os.path.basename(path))
                save_wav(out_dir, os.path.basename(path), payload)
            except RecorderError as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                failures += 1
            time.sleep(args.gap)
    print(f"\nsession done: {failures} failure(s)")
    return 1 if failures else 0


def cmd_mic(rec: XaioRecorder, args: argparse.Namespace) -> int:
    """Ask the sketch to read 200 ms from the PDM mic (no SD card needed)."""
    rec.send("mic")
    deadline = time.time() + 15.0
    while time.time() < deadline:
        line = rec.read_line(timeout=max(0.1, deadline - time.time()))
        if line.startswith("MIC ") or line.startswith("ERR"):
            print(line)
            return 0 if line.startswith("MIC ") else 1
    raise RecorderError("timeout waiting for MIC from the recorder")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive the XIAO wav_recorder over USB serial and pull its WAVs."
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"serial port (default {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="ignored by the S3 CDC")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ls = sub.add_parser("ls", help="list the files on the SD card")
    p_ls.set_defaults(func=cmd_ls)

    p_mic = sub.add_parser("mic", help="read 200 ms from the PDM mic (bring-up probe)")
    p_mic.set_defaults(func=cmd_mic)

    p_pull = sub.add_parser("pull", help="pull WAVs already on the card")
    p_pull.add_argument("--out", default="/tmp/xiao_takes", help="output directory")
    p_pull.add_argument("--prefix", default=None, help="only pull files starting with this")
    p_pull.set_defaults(func=cmd_pull)

    p_rec = sub.add_parser("rec", help="record N takes of one label and pull them")
    p_rec.add_argument("label")
    p_rec.add_argument("--takes", type=int, default=1)
    p_rec.add_argument("--out", default=None, help="output directory")
    p_rec.add_argument("--seconds", type=float, default=5.0, help="recording length")
    p_rec.add_argument("--delay", type=float, default=2.0, help="wait before speaking")
    p_rec.add_argument("--gap", type=float, default=1.5, help="wait between takes")
    p_rec.set_defaults(func=cmd_rec)

    p_sess = sub.add_parser("session", help="record every trained label (the T28 pass)")
    p_sess.add_argument("--labels", default="wake,noise,la,lb")
    p_sess.add_argument("--takes", type=int, default=3)
    p_sess.add_argument("--out", default="/tmp/xiao_takes")
    p_sess.add_argument("--seconds", type=float, default=5.0)
    p_sess.add_argument("--delay", type=float, default=2.5)
    p_sess.add_argument("--gap", type=float, default=1.5)
    p_sess.set_defaults(func=cmd_session)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with XaioRecorder(args.port, args.baud) as rec:
            rec.drain(0.2)
            rec.hello()
            return args.func(rec, args)
    except RecorderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
