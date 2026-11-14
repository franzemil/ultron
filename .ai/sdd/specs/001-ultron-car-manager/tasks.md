# Tasks — 001 Ultron Car Manager

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-001, FR-002, FR-004, FR-005, FR-006 | T10b, T11 |
| FR-003, FR-015 | T8, T10b, T11 |
| FR-007 | T10b, T11 |
| FR-008 | T10b, T11, T12 |
| FR-009, NFR-009 | T10c, T11, T12 |
| FR-010, FR-011 | T9 |
| FR-012 | T13 |
| FR-013, FR-016 | T3, T8 |
| FR-014 | T12 |
| FR-017 | T10c, T11 |
| NFR-001, NFR-002 | T9, T10b, T11, T12 |
| NFR-003, NFR-005 | T4, T5, T8 |
| NFR-006 | T2, T8, T14 |
| NFR-007 | T6, T9 |
| NFR-008 | T10b, T11, T12 |
| US-001 | T10b, T10c, T11, T12 |
| US-002 | T4 |
| US-003 | T7, T8, T9 |
| US-004 | T9, T10b, T11 |
| US-005 | T12 |
| US-006 | T13 |

## Implementation readiness check

- [ ] `.status` reads `requirements:approved` (currently `requirements:draft`)
- [ ] `.status` reads `design:approved`
- [ ] `.status` reads `tasks:approved` — **gates all implementation work**
- [ ] Q-011 (wiper stop behavior) resolved — Q-001 registry is already resolved
- [ ] No open questions remain in requirements.md

Implementation must be executed in dependency order; each task is verified by its own acceptance
criteria before the next begins. Spikes (T10a) never mark other tasks complete.

## Tasks

### T1 — Project scaffold & governance docs ✅ (complete)
- Covers: project conventions; SDD entry layer. Estimate: done. Deps: —.
- **"Governance"** = the repo's rulebook and shared memory — the files that tell every human or
  AI agent how this project runs: `AGENTS.md` (rules), `.ai/steering/*` (product, stack,
  conventions, principles), the spec with its approval gates (`.status`), and `CHANGELOG.md`.
  It exists so any session starts from the same context instead of guessing.
- Checklist: AGENTS.md; 4 steering docs; PLAN.md; INDEX.md; .status; spec 001 docs; .gitignore;
  README; CHANGELOG stub (PR-generated).
- Verification: `test -f .ai/sdd/specs/001-ultron-car-manager/.status` → `requirements:draft`.

### T2 — uv workspace + CLI skeleton (2 h)
- Covers: NFR-006. Deps: T1. Priority: Must.
- Checklist: root `pyproject.toml` (uv workspace, members `cli/` + `model/`), `cli/pyproject.toml`
  (package `ultron`, Typer), `model/pyproject.toml` (package `ultron-model`, workspace dependency
  of cli), config loader (`.env`, EI key), logging setup, `ultron --help` works, pytest scaffold.
- Likely files: `pyproject.toml`, `cli/pyproject.toml`, `model/pyproject.toml`,
  `cli/src/ultron/cli.py`, `tests/test_cli.py`.
- Verify: `uv sync && uv run ultron --help` exits 0; `uv run pytest` green.

### T3 — Dataset tooling + registry (3 h)
- Covers: FR-013, FR-016. Deps: T2. Priority: Must.
- Checklist: `dataset ingest` (copy/label WAVs from ANY source — device recordings **and**
  owner-supplied external recordings, e.g. phone/PC; mic source tagged in the filename,
  `lm_xiao_01.wav` / `lm_ext_01.wav`), `dataset validate` (16k/16-bit/mono checks,
  exit codes per file), `dataset dcfix`, `registry.json` schema + validator, label slugs.
- Likely files: `cli/src/ultron/dataset.py`, `model/registry.json`, `tests/test_dataset.py`.
- Verify: `uv run ultron dataset validate` fails loudly on a corrupt fixture and passes a valid one.

### T4 — Recorders for both devices (4 h)
- Covers: US-002, NFR-005. Deps: T2. Priority: Must.
- Checklist: port `wav_recorder` sketch from lampara-aliz (PSRAM=opi, serial `rec {label}`);
  Pi recorder script (`devices/orangepi/record.py`, sounddevice, 16 k/16-bit/mono, label arg); docs.
- Likely files: `devices/esp32/wav_recorder/wav_recorder.ino`, `devices/orangepi/record.py`,
  `docs/recording.md`.
- Verify: XIAO records a 5 s WAV to SD (DONE response); Pi recorder writes a valid WAV
  (`ultron dataset validate` passes it).

### T5 — Seeded augmentation (2 h)
- Covers: NFR-005. Deps: T3. Priority: Must.
- Checklist: 4 ffmpeg variants per original (gain 0.7–1.3, tempo 0.92–1.08, pitch ±6%,
  background mix, occasional echo) — lampara-aliz recipe, seeded; deterministic filenames.
- Likely files: `cli/src/ultron/augment.py`, `tests/test_augment.py`.
- Verify: same seed ⇒ byte-identical plan; `augment plan` lists targets before writing.

### T5.1 — Synthetic data generator (3 h)
- Covers: NFR-005 (supplementary data only). Deps: T5. Priority: Should.
- "Synthetic" = machine-generated training audio, like lampara-aliz's `tts` + `noise build`
  commands: (a) `ultron synth tts` renders every registered phrase in several Spanish TTS voices
  × speaking speeds (seeded); (b) `ultron synth noise` assembles a noise corpus (room ambience,
  car/engine sounds) for background mixing.
- Policy (lampara-aliz lesson): synthetic clips are tagged `synthetic`, NEVER enter the fresh
  hold-out, never count toward NFR-005 original minimums, and are used in training only via
  `--with-synthetic` (default **off** — owner decides per run); every use is a changelog event.
- Checklist: `synth tts` (multi-voice, seeded), `synth noise`, synthetic tagging in the dataset
  layout, dry-run plan before writing.
- Likely files: `cli/src/ultron/synth.py`, `tests/test_synth.py`, `docs/recording.md`.
- Verify: same seed ⇒ identical synthetic set; hold-out validator rejects `synthetic` clips;
  `synth tts --dry-run` lists targets without writing.

### T6 — Shared C MFCC library + parity (4 h)
- Covers: NFR-007 (DSP clause), principle 2. Deps: T3 (needs real WAVs). Priority: Must.
- Checklist: `dsp/mfcc.{h,c}` (params per design); compile on host; Python wrapper for features;
  extract reference vectors from lampara-aliz EI extraction; parity test; firmware-ready build.
- Likely files: `dsp/mfcc.h`, `dsp/mfcc.c`, `dsp/Makefile`, `tests/test_dsp_parity.py`.
- Verify: `uv run pytest tests/test_dsp_parity.py` — per-frame max abs diff ≤ 1e-3.

### T7 — Feature extraction → feature archive (2 h)
- Covers: US-003, NFR-006. Deps: T6. Priority: Must.
- **"npz"** = a NumPy archive file — one compressed file holding several arrays together. Here it
  packages the whole training set: the float32 MFCC feature matrix, each row's label, and the
  source file path, so PyTorch can load it in one step.
- Checklist: `ultron features` builds `build/features.npz` (float32 features + labels +
  paths) via the C library; NaN check fails loudly; split hash recorded.
- Likely files: `cli/src/ultron/features.py`, `tests/test_features.py`.
- Verify: `uv run ultron features` succeeds on fixture set; re-run ⇒ identical file hash.

### T8 — PyTorch training + threshold sweep (4 h)
- Covers: FR-003, FR-013, FR-015, NFR-003, NFR-006. Deps: T7, **Q-001 resolved**. Priority: Must.
- Checklist: `model/model.py` (design architecture), `model/train.py` (seeded split, class
  weights, early stop), phrase variants share one class per command (TD-010) with per-variant
  counts validated (NFR-005), synthetic clips mixed in only via `--with-synthetic` (T5.1,
  default off), fresh hold-out scoring, `ultron train run` + `train sweep` writing
  `model/thresholds.json`; PR description records dataset counts + versions + seed.
- Likely files: `model/model.py`, `model/train.py`, `cli/src/ultron/train.py`,
  `model/thresholds.json`, `tests/test_train_smoke.py`.
- Verify: 2-epoch smoke run on fixtures completes; hold-out report generated; seed rerun ⇒ same
  split hash and metrics.

### T9 — Export: ONNX → TFLite + parity gate (4 h)
- Covers: FR-010, FR-011, NFR-007. Deps: T8. Priority: Must.
- Checklist: first item is a conversion smoke test on a randomly initialized model (risk
  mitigation); `ultron export tflite` (onnx → onnx2tf → int8 full-quant + float16), representative
  dataset from features, parity report ≥ 99% else fail, `build/labels.json` order check; sizes
  reported vs NFR-002 budget.
- Likely files: `model/export.py`, `cli/src/ultron/export.py`, `tests/test_export_smoke.py`.
- Verify: `uv run ultron export tflite` produces `model_int8.tflite`, `model_fp16.tflite`,
  `parity_report.md` with agreement ≥ 99%; deliberately corrupted-parity fixture fails the gate.

### T10 — XIAO firmware (6 h, split)
**T10a — TFLM bring-up spike (2 h, labeled SPIKE)** · Deps: T1 (runs early, parallel to T8).
- Checklist: install candidate TFLM Arduino library (TD-004); run a canned sine-model inference
  on XIAO; measure arena + inference time; write findings to `docs/spike_tflm.md`.
- Verify: spike doc exists with go/no-go on TD-004 and measured numbers.
**T10b — Firmware core (3 h)** · Covers: FR-001…FR-008, NFR-001, NFR-002, NFR-008. Deps: T6, T9,
  T10a. Priority: Must.
- Checklist: PDM capture (2 s ring, 500 ms hop), C MFCC build, TFLM interpreter + AllOpsResolver,
  state machine (design table), event contract serial lines, `DBG pred=` lines, static buffers,
  `devices/esp32/ultron_voice/config.h` (thresholds + registry injected at build).
- Likely files: `devices/esp32/ultron_voice/ultron_voice.ino`,
  `devices/esp32/ultron_voice/dsp/*`, `devices/esp32/ultron_voice/config.h`.
- Verify: US-001 AC1–AC3 executed live against the device (event log captured); single-window
  inference ≤ 400 ms logged; serial emits contract lines only.

**T10c — Firmware output controller + relays (3 h)** · Covers: FR-009, FR-017, NFR-009. Deps:
  T10b, T3 (registry). Priority: Must.
- Checklist: 9-channel GPIO layer, behavior engines (`steady` / `blink_45s` /
  `blink_until_cancel` / `cancel` / `motor` / `pulse_2s` / `pulse_05s`), luz hierarchy, GD/GI-vs-P
  priority rules, 50 ms non-blocking tick, boot-safe outputs, `DBG out=` lines, pin allocation
  documented (PCF8574 fallback per TD-009).
- Likely files: `devices/esp32/ultron_voice/outputs.{h,cpp}`, `devices/esp32/ultron_voice/pins.h`.
- Verify: scripted commands produce contract-conformant channel timing (blink 1 s/1 s × 45 s,
  A = 2 s ±50 ms, CC = 0.5 s ±50 ms, CP stops parqueo < 200 ms); all channels OFF at boot.

### T11 — Orange Pi runtime + outputs (5 h)
- Covers: FR-001…FR-009, FR-015, FR-017, NFR-001, NFR-008, NFR-009. Deps: T6, T9. Priority: Must.
- Checklist: `devices/orangepi/ultron_pi.py` — sounddevice capture (16 k/16-bit), ctypes MFCC
  binding, tflite_runtime interpreter (float16/int8), same state machine semantics, JSON event
  lines to stdout, fail-fast mic listing (Q-003); output controller via libgpiod driving the
  3.3 V relay board — same behavior engines as T10c (divergence between targets is a bug);
  systemd user-service snippet in docs.
- Likely files: `devices/orangepi/ultron_pi.py`, `devices/orangepi/outputs.py`,
  `devices/orangepi/Makefile` (builds `dsp` .so), `docs/pi.md`.
- Verify: same US-001 AC1–AC3 script passes on the Pi; inference ≤ 200 ms logged; event JSON
  validates against contract.

### T12 — Benchmark harness + device decision (3 h)
- Covers: FR-014, NFR-004, US-005. Deps: T10b, T11. Priority: Must.
- Checklist: `ultron bench run --device xiao|pi` parses event + output logs; latency, confusion
  matrix (incl. luz triple and guiñador pair per Q-002), 30 min false-accept counter, memory
  stats, output timing checks (FR-009 tolerances); writes `docs/bench.md` table; device decision
  block with justification.
- Likely files: `cli/src/ultron/bench.py`, `docs/bench.md`.
- Verify: bench table populated for both devices from real logs; NFR-004 targets evaluated.

### T13 — Edge Impulse one-way sync (2 h)
- Covers: FR-012, US-006. Deps: T3, T9. Priority: Should.
- Checklist: `ultron ei sync` (EI API via `.env` key; upload originals with labels + registry
  metadata + current int8 model), dry-run mode, post-sync local checksum comparison, refuses any
  download/overwrite of local artifacts.
- Likely files: `cli/src/ultron/ei_sync.py`, `tests/test_ei_sync.py` (mocked API).
- Verify: dry-run prints target list; real run uploads to EI project; local checksums identical.

### T14 — Docs + release bookkeeping (2 h)
- Covers: NFR-006, US-005. Deps: all. Priority: Should.
- Checklist: README final; `docs/model.md`, `docs/retraining.md`, `docs/troubleshooting.md`;
  CHANGELOG verified as PR-generated (no hand edits); `review.md` written with evidence; bump
  `.status` per phase.
- Verify: all doc links resolve; every inference-affecting change has a conventional PR title.

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).
