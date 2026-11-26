#!/usr/bin/env bash
#
# deploy_xiao.sh — one command to compile, flash and (optionally) capture the
# XIAO ESP32S3 Sense console. Spec 003-can-act T12; also usable by T14/T15/T28.
#
#   ./scripts/deploy_xiao.sh                 # compile + flash, no capture
#   ./scripts/deploy_xiao.sh --listen 40     # compile + flash + 40 s console log
#   ./scripts/deploy_xiao.sh --selftest --listen 40
#   ./scripts/deploy_xiao.sh --dry-run       # print the commands, touch nothing
#
# Why one file: the raw sequence is three tools with different failure modes
# (arduino-cli compile → preflight read → arduino-cli upload → serial read), and
# the order matters — a compile error must never reach the port, and a silent
# port must never be retried. Those rules live here instead of in your head.
#
# LOCKING: off by default (--lock enables it). The lock only matters when more
# than one worker touches the single shared board; if the lock file is currently
# held by someone else this script says so and then does what you asked anyway.
# Use --lock when T14/T15/T28 are active on the same machine:
#     lockf -k /tmp/ultron-xiao.lock /bin/sh -c '…'
#
# Exit codes: 0 ok | 1 device silent/hung | 2 usage/setup | 3 compile failed
#             4 upload failed | 5 no console output during --listen

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKETCH="${REPO_ROOT}/devices/esp32/ultron_voice"
LIBRARIES="${REPO_ROOT}/devices/esp32/libraries"  # UltronVoice (voice pipeline)
FQBN="esp32:esp32:XIAO_ESP32S3"
PORT="/dev/cu.usbmodem2101"
LOCKFILE="/tmp/ultron-xiao.lock"
LOG_DIR="${TMPDIR:-/tmp}"; LOG_DIR="${LOG_DIR%/}/ultron-xiao-logs"
LISTEN_SECONDS=0
SELFTEST=0
USE_LOCK=0
DRY_RUN=0
SKIP_PREFLIGHT=0
SKIP_COMPILE=0
COMPILE_ONLY=0

usage() {
  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --port DEV        serial device (default /dev/cu.usbmodem2101)
  --listen SECONDS  after flashing, capture the console for SECONDS and save it
  --selftest        build/flash with -DULTRON_SELFTEST=1 (scripted scores,
                    real EVT lines — plumbing proof, NOT recognition)
  --lock            hold /tmp/ultron-xiao.lock for the whole run (needed when
                    other workers use the board; off by default)
  --skip-preflight  flash without the "is the chip alive?" read
  --skip-compile    upload the last build instead of recompiling
  --compile-only    compile and stop (no port, no flash — safe while others own
                    the board, and what CI would run)
  --dry-run         print every command, execute none
  -h, --help        this text
EOF
}

log()  { printf '\033[1m== %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mxx %s\033[0m\n' "$*" >&2; exit "${2:-2}"; }

# ── arg parsing ─────────────────────────────────────────────────────────────
ORIGINAL_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)            PORT="${2:?--port needs a value}"; shift 2 ;;
    --port=*)          PORT="${1#*=}"; shift ;;
    --listen)          LISTEN_SECONDS="${2:?--listen needs seconds}"; shift 2 ;;
    --listen=*)        LISTEN_SECONDS="${1#*=}"; shift ;;
    --selftest)        SELFTEST=1; shift ;;
    --lock)            USE_LOCK=1; shift ;;
    --skip-preflight)  SKIP_PREFLIGHT=1; shift ;;
    --skip-compile)    SKIP_COMPILE=1; shift ;;
    --compile-only)    COMPILE_ONLY=1; shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    *)                 die "unknown option: $1 (try --help)" ;;
  esac
done

# --lock: re-exec ourselves once under lockf, passing the original argv.
# (macOS lockf takes a command + args, not a shell string — no -c.) The env
# marker stops the recursion.
if [[ "$USE_LOCK" == "1" && -z "${ULTRON_UNDER_LOCK:-}" ]]; then
  command -v lockf >/dev/null || die "lockf not found (macOS built-in expected)"
  log "taking ${LOCKFILE}"
  exec env ULTRON_UNDER_LOCK=1 lockf -k "$LOCKFILE" "${BASH_SOURCE[0]}" "${ORIGINAL_ARGS[@]}"
fi

[[ -d "$SKETCH" ]] || die "sketch not found: $SKETCH"
command -v arduino-cli >/dev/null || die "arduino-cli not on PATH"
[[ "$LISTEN_SECONDS" =~ ^[0-9]+$ ]] || die "--listen wants whole seconds"

# Without --lock, at least tell the operator if someone else owns the board.
if [[ "$USE_LOCK" != "1" && "$DRY_RUN" != "1" ]]; then
  if ! lockf -k -t 0 "$LOCKFILE" /usr/bin/true 2>/dev/null; then
    warn "${LOCKFILE} is held by another worker — flashing anyway (no --lock)."
    warn "If a teammate is mid-upload, stop and re-run with --lock."
  fi
fi

if [[ "$COMPILE_ONLY" != "1" && "$DRY_RUN" != "1" ]]; then
  [[ -e "$PORT" ]] || die "port ${PORT} does not exist (board unplugged?)"
fi

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '   [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# ── 1. compile (never touches the port) ─────────────────────────────────────
# One optional flag, kept as a plain string: bash 3.2 + set -u breaks on empty
# arrays ("${arr[@]}: unbound variable"), and arduino-cli accepts --flag=value.
BUILD_PROP=""
[[ "$SELFTEST" == "1" ]] && BUILD_PROP="--build-property=compiler.cpp.extra_flags=-DULTRON_SELFTEST=1"

if [[ "$SKIP_COMPILE" == "1" ]]; then
  log "compile: skipped (--skip-compile)"
else
  LABEL=""
  if [[ "$SELFTEST" == "1" ]]; then LABEL=" [selftest]"; fi
  log "compile ${FQBN}${LABEL}"
  if [[ "$DRY_RUN" == "1" ]]; then
    run arduino-cli compile --libraries "$LIBRARIES" --fqbn "$FQBN" $BUILD_PROP "$SKETCH"
  else
    COMPILE_OUT="$(arduino-cli compile --libraries "$LIBRARIES" --fqbn "$FQBN" $BUILD_PROP "$SKETCH" 2>&1)" || {
      printf '%s\n' "$COMPILE_OUT" >&2
      die "compile failed — nothing was flashed" 3
    }
    printf '%s\n' "$COMPILE_OUT" | grep -E "Sketch uses|Global variables" || true
  fi
fi

if [[ "$COMPILE_ONLY" == "1" ]]; then
  log "compile-only: done (nothing flashed, port untouched)"
  exit 0
fi

# ── 2. preflight: does the chip answer at all? ──────────────────────────────
# A port that enumerates but returns zero bytes is a hung chip; uploading into
# that is what hangs it harder. This is the one rule worth enforcing.
if [[ "$SKIP_PREFLIGHT" == "1" || "$DRY_RUN" == "1" ]]; then
  log "preflight: skipped"
else
  log "preflight: reading ${PORT} for 5 s"
  set +e
  python3 "${REPO_ROOT}/scripts/serial_log.py" --port "$PORT" --seconds 5 --quiet
  PREFLIGHT_RC=$?
  set -e
  case "$PREFLIGHT_RC" in
    0) log "preflight: chip is talking" ;;
    1) die "chip is SILENT (port enumerates, zero bytes). Hung device — needs a
   physical reset. Do NOT retry uploads in a loop. Nothing was flashed." 1 ;;
    *) die "preflight could not read ${PORT} (rc=${PREFLIGHT_RC})" 2 ;;
  esac
fi

# ── 3. upload ───────────────────────────────────────────────────────────────
log "flash → ${PORT}"
if ! run arduino-cli upload -p "$PORT" --libraries "$LIBRARIES" --fqbn "$FQBN" $BUILD_PROP "$SKETCH"; then
  die "upload failed. If esptool says 'No serial data received', the chip is hung:
   physical reset required. Do not retry in a loop." 4
fi

# ── 4. optional console capture ─────────────────────────────────────────────
if [[ "$LISTEN_SECONDS" -gt 0 ]]; then
  if [[ "$DRY_RUN" != "1" ]]; then
    # The chip's USB-Serial/JTAG re-enumerates after --after hard-reset; give the
    # host a moment before claiming the port, or the first read sees a dying fd.
    sleep 2
  fi
  mkdir -p "$LOG_DIR"
  SUFFIX=""
  if [[ "$SELFTEST" == "1" ]]; then SUFFIX="-selftest"; fi
  LOG="${LOG_DIR}/xiao-$(date +%Y%m%d-%H%M%S)${SUFFIX}.log"
  log "capture: ${LISTEN_SECONDS}s → ${LOG}"
  set +e
  if [[ "$DRY_RUN" == "1" ]]; then
    run python3 "${REPO_ROOT}/scripts/serial_log.py" --port "$PORT" \
      --seconds "$LISTEN_SECONDS" --raw
  else
    python3 "${REPO_ROOT}/scripts/serial_log.py" --port "$PORT" \
      --seconds "$LISTEN_SECONDS" --raw | tee "$LOG"
  fi
  CAPTURE_RC=${PIPESTATUS[0]}
  set -e
  if [[ "$CAPTURE_RC" != "0" ]]; then
    die "no console output after flashing — check ${PORT}, then run:
   python3 scripts/serial_log.py --seconds 20" 5
  fi
  log "summary"
  printf '   EVT lines : %s\n' "$(grep -c '^EVT ' "$LOG" 2>/dev/null || echo 0)"
  printf '   DBG pred= : %s\n' "$(grep -c '^DBG pred=' "$LOG" 2>/dev/null || echo 0)"
  printf '   log       : %s\n' "$LOG"
  grep -E '^EVT |^DBG mem |^DBG model ' "$LOG" 2>/dev/null | head -20 || true
else
  log "done — flash in place. Capture with:"
  printf '   python3 scripts/serial_log.py --seconds 40\n'
fi
