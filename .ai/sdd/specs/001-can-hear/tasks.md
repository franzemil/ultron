# Tasks — 001 Can Hear (Milestone 1)

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-016, FR-020, FR-021, FR-022 | T3 |
| NFR-005 | T4, T5, T5.1 |
| NFR-006 | T2 |
| NFR-007 (DSP clause) | T6 |
| US-002 | T4 |

This spec **is** Milestone 1 (can hear) — see [.ai/sdd/PLAN.md](../../PLAN.md). Siblings: spec 002
(Milestone 2 — Can think), spec 003 (Milestone 3 — Can act), spec 004 (Milestone 4 — Can decide),
spec 005 (Milestone 5 — Archived).

## Implementation readiness check

- [x] `.status` reads `requirements:approved` ✅ (owner-approved)
- [x] `.status` reads `design:approved` ✅ (owner-approved)
- [x] `.status` reads `tasks:approved` — **gates all implementation work** ✅ (owner-approved 2026-11-14)
- [x] No open questions remain in requirements.md (Q-003/Q-007 resolved)

Implementation must be executed in dependency order; each task is verified by its own acceptance
criteria before the next begins.

## Tasks

### T1 — Project scaffold & governance docs ✅ (complete)
- Covers: project conventions; SDD entry layer — now spanning all five milestone specs. Estimate:
  done. Deps: —.
- **"Governance"** = the repo's rulebook and shared memory: `AGENTS.md` (rules), `.ai/steering/*`
  (product, stack, conventions, principles), the specs with their approval gates (`.status`), and
  `CHANGELOG.md`. It exists so any session starts from the same context instead of guessing.
- Checklist: AGENTS.md; 4 steering docs; PLAN.md; INDEX.md; .status; milestone specs 001–005;
  .gitignore; README; CHANGELOG stub (PR-generated).
- Verification: `test -f .ai/sdd/specs/001-can-hear/.status` → `requirements:draft`.

### T2 — uv workspace + CLI skeleton (2 h)
- Covers: NFR-006. Deps: T1. Priority: Must.
- Checklist:
  - [x] root `pyproject.toml` (uv workspace, members `cli/` + `model/`)
  - [x] `cli/pyproject.toml` (package `ultron`, Typer)
  - [x] `model/pyproject.toml` (package `ultron-model`, workspace dependency of cli)
  - [x] config loader (`.env`, EI key)
  - [x] logging setup
  - [x] `ultron --help` works
  - [x] pytest scaffold
- Likely files: `pyproject.toml`, `cli/pyproject.toml`, `model/pyproject.toml`,
  `cli/src/ultron/cli.py`, `tests/test_cli.py`.
- Verify: `uv sync && uv run ultron --help` exits 0; `uv run pytest` green.

### T3 — Dataset tooling + registry (3 h)
- Covers: FR-013 (schema support), FR-016, FR-020, FR-021, FR-022. Deps: T2. Priority: Must.
- [x] Checklist: `dataset ingest` (copy/label WAVs from ANY source — device recordings **and**
  owner-supplied external recordings; mic source tagged in the filename, `lm_xiao_01.wav` /
  `lm_ext_01.wav`), `dataset validate` (16k/16-bit/mono checks, exit codes per file),
  `dataset dcfix`, `registry.json` schema + validator, label slugs.
- Likely files: `cli/src/ultron/dataset.py`, `model/registry.json`, `tests/test_dataset.py`.
- Verify: `uv run ultron dataset validate` fails loudly on a corrupt fixture and passes a valid
  one.

### T4 — Recorders for both devices (4 h) ✅
- Covers: US-002, NFR-005. Deps: T2. Priority: Must.
- [x] Checklist: port `wav_recorder` sketch from lampara-aliz (PSRAM=opi, serial `rec {label}`);
  Pi recorder script (`devices/orangepi/record.py`, sounddevice, 16 k/16-bit/mono, label arg);
  docs.
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

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).

### T8 — Interactive recorder CLI `ultron record` (3 h)
- Covers: FR-023, FR-024, FR-025, FR-026; US-002. Deps: T4. Priority: Must.
- Checklist: `cli/src/ultron/record.py` — new `record` Typer subcommand; rich-based
  interactive code selection from `model/registry.json`; record `--seconds` per attempt
  until `--samples` examples per code; write `{label}_local_{nn:02d}.wav` (mic tag local);
  validate codes against registry; host-side tests.
- Likely files: `cli/src/ultron/record.py`, `tests/test_record_cli.py`.
- Verify: `uv run ultron record --help` exits 0; `uv run pytest tests/test_record_cli.py`
  green; recorded WAVs pass `ultron dataset validate`.
