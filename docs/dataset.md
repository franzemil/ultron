# Dataset tooling — `ultron dataset`

`ingest` / `validate` / `dcfix` (FR-020 / FR-021 / FR-022). See
`.ai/sdd/specs/001-can-hear/tasks.md` (T3 — `tasks:approved`).

## Layout

Dataset lives in `dataset/` (gitignored — the tree IS the labeling, no
manifest JSON):

```
dataset/
  originals/<label>/*.wav    # {label}_{mic}_{nn}.wav, mic in {xiao, pi, ext}
  augmented/<label>/aug_*.wav
  holdout/<label>/*.wav
```

Per-file labeling comes from the tree itself; `dataset validate` walks the
filesystem and cannot disagree with what is on disk.

## `dataset ingest` (FR-020)

Copies and labels WAVs from **ANY** source — device recordings and
owner-supplied external recordings (phone/PC). The mic source is tagged in
the filename: `lm_xiao_01.wav` / `lm_ext_01.wav`.

```
ultron dataset ingest <source> <label> --mic xiao|pi|ext [--dest dataset/originals]
```

Label must be a lowercase ASCII slug (`lm`, `lb`, `gd`, `wake`, `noise`, …).

## `dataset validate` (FR-021)

Checks every clip is **16 kHz / 16-bit / mono**. Exits non-zero listing each
failing file, so corrupt data never trains silently.

```
ultron dataset validate [dataset]   # exit 0 = all valid, exit 1 = failures
```

## `dataset dcfix` (FR-022) — why we need it

Microphones (especially the XIAO PDM mic and cheap USB mics) can add a
constant voltage to every recorded sample, shifting the whole waveform up or
down away from zero — that is **"DC offset"**.

If left in place, every MFCC coefficient is computed on a skewed signal, so
features drift from what the on-device DSP actually produces and training
silently degrades (lampara-aliz lesson: NaN → DSP error −5, no silent
training on corrupt data).

`dataset dcfix` subtracts that constant (the waveform mean) from each int16
sample, recentering the signal around zero before feature extraction:

```
ultron dataset dcfix [dataset]   # in place; skips non-16-bit/unreadable files
```

Run `dcfix` **before** feature extraction / training.

## Registry (FR-016)

`model/registry.json` — 10 actions + cancel (`cp`), `wake`, `noise`,
language `es`, version 2, cap 10 actions / 13 model classes. Validated by
`validate_registry()`.
