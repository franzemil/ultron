# Design — 001 Can Hear (Milestone 1)

## Executive summary

A uv workspace with a `ultron` Typer CLI ingests, validates, and repairs the dataset
(owner-supplied external recordings; the device recorders moved to spec 003); seeded ffmpeg
augmentation (and an
opt-in synthetic generator) multiply the corpus; and one shared C MFCC library — compiled three
ways from the same bytes — guarantees train-vs-device feature parity against the lampara-aliz EI
reference.

## Requirements mapping

| FR / NFR | Design section |
|---|---|
| FR-016, FR-020–FR-022 | [Data model](#data-model) (registry.json, dataset layout), [CLI](#cli-scaffold) |
| NFR-005 | [Data model](#data-model) (layout supports counts), [Verification](#verification-strategy) |
| NFR-006 | [Conventions](../../../.ai/steering/conventions.md) (seeds, changelog) |
| NFR-007 (DSP clause) | [DSP](#dsp), TD-003, TD-006 |

## CLI scaffold

Root `pyproject.toml` (uv workspace, members `cli/` + `model/`), `cli/pyproject.toml` (package
`ultron`, Typer), `model/pyproject.toml` (package `ultron-model`, workspace dependency of cli),
config loader (`.env`, EI key), logging setup, pytest scaffold. The `model/` member ships empty in
Milestone 1 — its code arrives with spec 002.

## Data model

```
dataset/                          # gitignored — the tree IS the labeling; no manifest JSON
  originals/<label>/*.wav         # {label}_{nn}.wav — real recordings; filename tags the mic (xiao / ext)
  augmented/<label>/aug_*.wav     # 4 seeded ffmpeg variants per original (deterministic)
  holdout/<label>/*.wav           # fresh recordings, NEVER trained on (NFR-003) — isolation is structural
build/features.npz                  # float32 features + labels + file paths (cache, spec 002)
```

Why a folder tree instead of a manifest JSON: recordings arrive in bulk (SD-card pulls, batch
clips split per utterance), so per-file labeling must come from the tree itself; `dataset
validate` derives truth by walking the filesystem — it cannot disagree with what is on disk;
`holdout/` isolation is enforced by *location*, never by a flag that could silently leak data
into training; and `dataset/` is gitignored, so a manifest could never stay in sync across
machines. JSON is reserved for versioned semantics (`registry.json` below) and computed caches
(`features.npz`).

`model/registry.json` (versioned; D-005) — owner-resolved registry (Q-001), built and validated
by T3:

```json
{
  "version": 2,
  "language": "es",
  "wake":   { "id": "wake", "phrase": ["hey ultron"] },
  "noise":  { "id": "noise" },
  "commands": [
    { "id": "lm",  "sigla": "LM",  "phrase": ["luz media", "enciende luz media"],                       "behavior": "steady",             "channels": ["lm"] },
    { "id": "lb",  "sigla": "LB",  "phrase": ["luz baja", "enciende luz baja"],                         "behavior": "steady",             "channels": ["lb", "lm"] },
    { "id": "la",  "sigla": "LA",  "phrase": ["luz alta", "enciende luz alta"],                         "behavior": "steady",             "channels": ["la", "lb", "lm"] },
    { "id": "gd",  "sigla": "GD",  "phrase": ["guiñador derecho", "enciende guiñador derecho"],         "behavior": "blink_45s",          "channels": ["gd"] },
    { "id": "gi",  "sigla": "GI",  "phrase": ["guiñador izquierdo", "enciende guiñador izquierdo"],     "behavior": "blink_45s",          "channels": ["gi"] },
    { "id": "p",   "sigla": "P",   "phrase": ["parqueo", "enciende parqueo"],                           "behavior": "blink_until_cancel", "channels": ["gd", "gi"] },
    { "id": "cp",  "sigla": "CP",  "phrase": ["cancelar parqueo"],                                      "behavior": "cancel",             "channels": [] },
    { "id": "lpb", "sigla": "LPB", "phrase": ["limpia parabrisas baja", "enciende limpia parabrisas baja"], "behavior": "motor_45s",       "channels": ["lpb"] },
    { "id": "lpa", "sigla": "LPA", "phrase": ["limpia parabrisas alta", "enciende limpia parabrisas alta"], "behavior": "motor_45s",       "channels": ["lpa"] },
    { "id": "a",   "sigla": "A",   "phrase": ["arranque", "enciende arranque"],                         "behavior": "pulse_2s",           "channels": ["a"] },
    { "id": "cc",  "sigla": "CC",  "phrase": ["cierre centralizado", "enciende cierre centralizado"],   "behavior": "pulse_05s",          "channels": ["cc"] }
  ],
  "cap": { "actions": 10, "model_classes": 13 }
}
```

Label set = sorted registry ids (`lm lb la gd gi p cp lpb lpa a cc`) + `wake` + `noise`; class
index order is frozen into the export and the device firmware (same array order everywhere) —
enforced from spec 002 onward. `model/thresholds.json` is introduced in spec 002 (Q-006).

## DSP

`dsp/mfcc.{h,c}` — the only MFCC implementation in the repo (principle 2), compiled three ways:

1. **Firmware**: compiled directly into `devices/esp32/ultron_voice/` (spec 003).
2. ~~**Pi runtime**: built as a shared library, loaded via `ctypes` (`devices/orangepi/`, spec 003).~~ *(withdrawn 2026-11-26: Pi out of scope)*
3. **Training**: built on the host, invoked by `ultron features` (numpy wrapper, spec 002) —
   training features come from the same bytes (lampara-aliz lesson, elevated to a principle).

Parameters (ported from the Edge Impulse MFCC proven in lampara-aliz): 16 kHz, int16 input,
frame 20 ms / stride 20 ms, 13 MFCC coefficients, **2.0 s analysis window → (100 × 13)**, computed
every 500 ms hop. Analysis-window length is 2 s because 3–4-word Spanish phrases do not fit 1 s
(TD-006).

**DSP parity**: `ultron features` also emits reference vectors; `tests/test_dsp_parity.py`
compares per-frame outputs against the lampara-aliz EI reference extraction (max abs diff ≤ 1e-3,
NFR-007 DSP clause).

## Technical decisions

**TD-003 — Shared C MFCC library** (principle 2). Alternatives: librosa/numpy-only features
(rejected: train/serve divergence risk — exactly what bit TinyML projects); TFLM built-in audio
frontend (rejected: MFCC not fully exposed in the Arduino library path).

**TD-006 — 2.0 s analysis window / 500 ms hop** (vs lampara-aliz's 1 s). Reason: 3–4-word Spanish
commands ("enciende guiñador izquierdo") exceed 1 s; wake utterance (~1 s) sits comfortably inside
a 2 s window. Cost: 2× features per window, still trivially small.

## Verification strategy

| Level | What | How |
|---|---|---|
| Unit | DSP parity | `tests/test_dsp_parity.py` vs EI reference vectors (≤ 1e-3/frame) |
| Unit | Registry schema | schema validation tests (registry.json, FR-016) |
| Integration | CLI | `ultron dataset validate` exit codes on corrupt/valid fixtures (FR-021) |
| Unit | Augmentation | same seed ⇒ byte-identical augmentation plan |
| Unit | Synthetic | same seed ⇒ identical synthetic set; hold-out validator rejects `synthetic` |

## Interactive recorder — `ultron record`

**CLI** — new `record` Typer subcommand under the `ultron` app:

```
ultron record [--seconds 5] [--samples 20] [--device DEVICE] [--out-dir dataset/originals]
```

- Reads `model/registry.json` for the recordable code list.
- Uses `rich` (`Confirm.ask`, `Prompt.ask`) for interactive code selection — no silent
  fallback to device 0; fails fast listing capture devices when no mic is present.
- Defaults: `--seconds 5`, `--samples 20` (matches NFR-005 ≥ 20 originals per variant).
- For each selected code: record `--seconds` of audio → save as
  `{label}_local_{nn:02d}.wav` → repeat until `--samples` files exist for that label.
- Validates each selected code against `model/registry.json` (FR-026); rejects unknown codes.
- Reuses the same 16 kHz / 16-bit / mono WAV writing as `devices/local/record.py` so host
  recordings are byte-identical to the standalone script output.
- Prints progress per attempt and a final summary; suggests `ultron dataset validate` after.

**Files:**
- `cli/src/ultron/record.py` — new `record` Typer subcommand module.
- `tests/test_record_cli.py` — host-side tests (prompt stubbed, WAV output validated against
  16 kHz / 16-bit / mono contract).

**Verification:**
- `uv run ultron record --help` exits 0.
- `uv run pytest tests/test_record_cli.py` green.
- Recorded WAVs pass `ultron dataset validate` (FR-021).


## Long-take recording + pyannote split — `ultron record-long` / `ultron split` (T9)

**CLI** — two new Typer subcommands under the `ultron` app:

```
ultron record-long --seconds 20 --label lb [--out-dir dataset/takes]
ultron split dataset/takes/take_001_lb.wav --label lb [--hf-token TOKEN]
```

- `record-long`: records N seconds (default 20) of continuous 16 kHz/16-bit/mono audio from the host mic into `dataset/takes/take_<nn>_<label>.wav`. One label per take.
- `split`: runs pyannote.audio Voice Activity Detection on the take, slices each speech segment into its own WAV, writes `{label}_local_<nn>.wav` under `dataset/originals/<label>/`.
- `--label` validated against `model/registry.json` (FR-030).
- Hugging Face token required for pyannote model download (`PYANNOTE_HF_TOKEN` env var or `--hf-token`) (FR-031).
- VAD-only pipeline — no STT, no diarization, no on-device speech models (NFR-011).
- Output WAVs are 16 kHz / 16-bit / mono; pass `ultron dataset validate` (FR-021).

**Files:**
- `cli/src/ultron/record_long.py` — `record-long` subcommand.
- `cli/src/ultron/split.py` — `split` subcommand (pyannote VAD + slicing).
- `tests/test_record_long.py`, `tests/test_split.py` — host-side tests (pyannote stubbed; fake segments).

**Verification:**
- `uv run ultron record-long --help` exits 0.
- `uv run ultron split --help` exits 0.
- `uv run pytest tests/test_split.py` green — fake pyannote segments produce valid 16k/16-bit/mono WAVs.
- `--label` validation rejects unknown codes.
- Missing HF token fails fast.

**Pyannote notes:**
- `pyannote.audio` requires `pip install pyannote.audio` and a Hugging Face account + token.
- Model: `pyannote/voice-activity-detection`.
- Heavy dependency (torch). Host dev machine only; not bundled in device firmware.
