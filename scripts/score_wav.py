"""Score one WAV through the trained T8 checkpoint — preview of T10.

Temporary stand-in for the not-yet-landed `ultron infer files` (T10): runs a
WAV through the same shared C MFCC windowing path the devices use, then the
trained checkpoint, and decodes each 2 s window with build/thresholds_demo.json
(or --thresholds). Superseded by `ultron infer` — do not extend.

Usage:
    uv run python scripts/score_wav.py <clip.wav> [--checkpoint build/checkpoint.pt]
                                       [--thresholds build/thresholds_demo.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo root (for the dsp/ C binding import that ultron.features performs).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch

from dsp.mfcc import Mfcc
from ultron.features import (
    WINDOW_LEN,
    _dc_correct,
    _load_wav,
    _window_features,
    _window_starts,
)
from ultron_model.model import KwsNet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav", type=Path)
    ap.add_argument("--checkpoint", type=Path, default=Path("build/checkpoint.pt"))
    ap.add_argument("--thresholds", type=Path, default=Path("build/thresholds_demo.json"))
    args = ap.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    classes = list(ckpt["classes"])
    model = KwsNet(num_classes=len(classes))
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    samples = _dc_correct(_load_wav(args.wav, args.wav.name))
    mfcc = Mfcc(16000)
    rows = []
    for start in _window_starts(len(samples)):
        if len(samples) < WINDOW_LEN:  # short clip: one zero-padded window
            window = np.zeros(WINDOW_LEN, dtype=np.int16)
            window[: len(samples)] = samples
        else:
            window = samples[start : start + WINDOW_LEN]
        rows.append(_window_features(window, mfcc))
    mfcc.close()
    x = np.stack(rows).astype(np.float32)
    with torch.no_grad():
        probs = model(torch.from_numpy(x)).numpy()

    th = json.loads(args.thresholds.read_text())

    def decode(row: np.ndarray) -> str:
        i = int(row.argmax())
        p = float(row[i])
        second = float(np.sort(row)[-2])
        label = classes[i]
        if label == "wake":
            t, m = th["wake"], th["margin_wake"]
        elif label in th["commands"]:
            t, m = th["commands"][label]["threshold"], th["commands"][label]["margin"]
        else:
            return "— (noise class: no wake/command)"
        return label if (p >= t and p - second >= m) else f"— (rejected: {label} {p:.2f})"

    print(f"{args.wav.name}: {len(rows)} window(s), classes {classes}")
    for k, row in enumerate(probs):
        scores = "  ".join(f"{c}={v:.2f}" for c, v in zip(classes, row))
        print(f"  [{k}] {scores}  ->  {decode(row)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
