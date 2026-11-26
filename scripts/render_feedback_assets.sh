#!/usr/bin/env bash
#
# render_feedback_assets.sh — render the two fixed confirmation clips (spec 003 T14).
#
# FR-018 / FR-037 / D-006 / Q-012: the clips are rendered ONCE, committed under
# assets/, and played byte-identically by every runtime. No target synthesizes
# speech at runtime. This script is the reproducibility record: run it only to
# re-create the committed WAVs, then commit the new bytes.
#
# Pipeline (macOS only — needs `say`, `ffmpeg`, `python3`):
#   say -v Mónica -r <rate> "<text>"          → AIFF at 22.05 kHz
#   ffmpeg → 16 kHz / mono / s16le, trim silence at both ends
#   python3 → peak of the trimmed PCM       → peak-normalise to -1 dBFS
#   ffmpeg → 4 ms fade in/out, write 16 kHz / 16-bit / mono WAV
#
# Budgets (NFR-010): escuchando ≤ 500 ms, comando ≤ 300 ms. The script fails if
# a clip lands outside its budget — a too-slow `say` rate is a hard error, not a
# surprise on the device.
#
# Voice choice: Mónica (es_ES) — the Spanish (Spain) voice named in the spec's
# toolchain notes. `say -v '?'` lists the alternatives (Paulina es_MX, Eddy/Flo
# es_ES, …); swapping the voice or rate means re-rendering AND re-running
# scripts/gen_feedback_clips.py, because the firmware embeds these exact bytes.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/assets"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

THRESHOLD="-45dB"        # silence trim threshold (Q-012 "silence-trimmed")
FADE_S="0.004"           # 4 ms — kills the click at either edge
PEAK_DBFS="-1"           # normalise the loudest sample to -1 dBFS

need() { command -v "$1" >/dev/null 2>&1 || { echo "render_feedback_assets: missing $1" >&2; exit 2; }; }
need say
need ffmpeg
need python3

# render <name> <budget_ms> <rate> <text>
render() {
  local name="$1" budget_ms="$2" rate="$3" text="$4"
  local voice="Mónica"
  local aiff="$TMP/$name.aiff" raw="$TMP/$name.pcm" wav="$OUT_DIR/$name.wav"

  say -v "$voice" -r "$rate" "$text" -o "$aiff"
  # 16 kHz / mono / s16le, silence trimmed at both ends.
  ffmpeg -y -loglevel error -i "$aiff" -ar 16000 -ac 1 -f s16le \
    -af "silenceremove=start_periods=1:start_threshold=$THRESHOLD:stop_periods=1:stop_threshold=$THRESHOLD:stop_duration=0.05" \
    "$raw"

  # Peak-normalise in the PCM domain, then fade and write the WAV.
  local scale
  scale="$(python3 - "$raw" "$PEAK_DBFS" <<'PY'
import struct, sys, math
raw, target_db = sys.argv[1], float(sys.argv[2])
data = open(raw, "rb").read()
n = len(data) // 2
samples = struct.unpack("<%dh" % n, data)
peak = max((abs(s) for s in samples), default=0)
if peak == 0:
    sys.exit("render_feedback_assets: clip is silent")
target = 32767.0 * (10.0 ** (target_db / 20.0))
print("%.4f" % min(target / peak, 8.0))
PY
)"
  ffmpeg -y -loglevel error -f s16le -ar 16000 -ac 1 -i "$raw" \
    -af "volume=${scale},afade=t=in:st=0:d=$FADE_S,afade=t=out:st=$(python3 -c "import os;print(max(0.0, os.path.getsize('$raw')/2/16000 - $FADE_S))"):d=$FADE_S" \
    -c:a pcm_s16le -ar 16000 -ac 1 "$wav"

  local samples_ms
  samples_ms="$(python3 - "$wav" <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", "not a RIFF/WAVE file"
pos, fmt, pcm = 12, None, None
while pos + 8 <= len(data):
    cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
    body = data[pos + 8:pos + 8 + size]
    if cid == b"fmt ":
        fmt = struct.unpack("<HHIIHH", body[:16])
    elif cid == b"data":
        pcm = body
    pos += 8 + size + (size & 1)
assert fmt and pcm is not None, "missing fmt/data chunk"
ch, rate, bits = fmt[1], fmt[2], fmt[5]
assert (ch, rate, bits) == (1, 16000, 16), f"format drift: {ch}ch {rate}Hz {bits}bit"
print(len(pcm) // 2 * 1000 // 16000)
PY
)"

  if [ "$samples_ms" -gt "$budget_ms" ]; then
    echo "render_feedback_assets: $name is ${samples_ms} ms > ${budget_ms} ms budget — raise the say rate" >&2
    exit 1
  fi
  printf '%-14s voice=%s rate=%s dur=%sms budget=%sms bytes=%s\n' \
    "$name" "$voice" "$rate" "$samples_ms" "$budget_ms" "$(wc -c < "$wav" | tr -d ' ')"
}

mkdir -p "$OUT_DIR"
# "Escuchando" (wake confirmation). rate 300 is the slowest rate that fits
# 500 ms with the tail trim: 180→688 ms, 220→565, 260→508, 300→441.
render escuchando 500 300 "Escuchando"
# "Comando" (command confirmation). rate 340 trims the 3-syllable word to 255 ms.
render comando 300 340 "Comando"
