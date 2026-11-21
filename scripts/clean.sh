#!/usr/bin/env bash
# scripts/clean.sh — remove everything this repo installs or generates locally.
#
# Safe by design: never touches source, uv.lock, .env, dataset/, or exported
# model files (*.onnx / *.tflite). Run it when the project is done to reclaim
# disk space; `uv sync` + `make -C dsp` recreate everything afterwards.
#
# Usage:
#   scripts/clean.sh              # list targets, then ask for confirmation
#   scripts/clean.sh --dry-run    # only list what would be removed
#   scripts/clean.sh --yes        # no prompt (CI / non-interactive)
#   scripts/clean.sh --uv-cache   # also run `uv cache clean` (shared global cache!)
#
# Exit codes: 0 success, 1 refused / error.

set -euo pipefail

REPO_ROOT="$(git -C "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

DRY_RUN=0
ASSUME_YES=0
UV_CACHE=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y)  ASSUME_YES=1 ;;
    --uv-cache) UV_CACHE=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "clean: unknown option '$arg' (try --help)" >&2
      exit 1
      ;;
  esac
done

# --- Targets ---------------------------------------------------------------
# Directories created by uv / pip / pytest / builds. Explicit list so nothing
# is removed by a broad wildcard.
DIRS=(
  ".venv"
  "build"
  "dist"
  "dsp/build"
  "cli/build"
  "model/build"
  ".pytest_cache"
  ".ruff_cache"
  ".mypy_cache"
  ".coverage"
  "htmlcov"
)

# Generated files (not installed packages, but produced by local commands).
FILES=(
  "dsp/mfcc_host_test.c"
  "coverage.xml"
)

# Everything below is discovered recursively but stays inside the repo.
while IFS= read -r -d '' d; do
  DIRS+=("${d#./}")
done < <(find . \
  \( -path ./.git -o -path ./.venv -o -path ./dataset \) -prune -o \
  -type d \( -name "__pycache__" -o -name "*.egg-info" \) -print0)

while IFS= read -r -d '' f; do
  FILES+=("${f#./}")
done < <(find . \
  \( -path ./.git -o -path ./.venv -o -path ./dataset \) -prune -o \
  -type f \( -name "*.pyc" -o -name "*.pyo" \) -print0)

# --- Report ----------------------------------------------------------------
existing=()
for p in "${DIRS[@]}" "${FILES[@]}"; do
  [[ -e "$p" || -L "$p" ]] && existing+=("$p")
done

if [[ ${#existing[@]} -eq 0 && $UV_CACHE -eq 0 ]]; then
  echo "clean: nothing to remove — already clean."
  exit 0
fi

echo "clean: targets under $REPO_ROOT"
for p in "${existing[@]}"; do
  if [[ -d "$p" ]]; then
    printf '  dir   %-45s %s\n' "$p" "$(du -sh "$p" 2>/dev/null | awk '{print $1}')"
  else
    printf '  file  %s\n' "$p"
  fi
done
if [[ $UV_CACHE -eq 1 ]]; then
  echo "  extra uv cache clean (shared global cache — affects other projects)"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "clean: dry run — nothing removed."
  exit 0
fi

# --- Confirm ---------------------------------------------------------------
if [[ $ASSUME_YES -eq 0 ]]; then
  if [[ ! -t 0 ]]; then
    echo "clean: no TTY for confirmation; re-run with --yes." >&2
    exit 1
  fi
  read -r -p "Remove the targets above? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "clean: aborted."; exit 1; }
fi

# --- Remove ----------------------------------------------------------------
for p in "${existing[@]}"; do
  rm -rf -- "$p"
done

if [[ $UV_CACHE -eq 1 ]] && command -v uv >/dev/null 2>&1; then
  uv cache clean
fi

echo "clean: done. Recreate with 'uv sync' and 'make -C dsp'."
