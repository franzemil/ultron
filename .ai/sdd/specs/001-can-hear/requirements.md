# Requirements — 001 Can Hear (Milestone 1)

> Part of the ultron system. Siblings: [002-can-think](../002-can-think/) (model + export),
> [003-can-act](../003-can-act/) (devices + contracts), [004-can-decide](../004-can-decide/)
> (bench), [005-archived](../005-archived/) (EI sync). FR/NFR IDs are **global** across all
> milestone specs.

## Overview

Milestone 1 builds ultron's **ears**: the local data pipeline. A uv-based CLI (`ultron`), dataset tooling
(ingest / validate / dcfix) with the owner-resolved command registry, seeded augmentation
(+ optional synthetic data), and the shared C MFCC library
proven frame-for-frame against the lampara-aliz Edge Impulse reference extraction.

The system being built overall: a Spanish voice-actuation car manager — wake on **"hey ultron"**,
then a command from a fixed registry of 10 car actuations + cancel (CP) drives **9 relay
channels** (LM LB LA GD GI LPB LPA A CC). Recognition runs entirely on one of two candidate
devices; a benchmark (spec 004) decides which one ships.

## Goals

1. One command ingests valid training audio from any source — external recordings now
   (`mic=ext`), device mics later (US-002, spec 003).
2. Dataset + registry are validated, reproducible, and never silently corrupt (lampara lessons).
3. MFCC features are identical everywhere — one C implementation, parity-proven vs EI reference.

## Non-Goals

- No model training or export here (spec 002).
- No device runtime/firmware behavior (spec 003) — the WAV recorders moved there too
  (amendment 2026-11-19, owner decision).
- No Edge Impulse upload here (spec 005).

## Shared system context (applies to every milestone spec)

- Wake phrase is fixed: **"hey ultron"**. All voice interaction is Spanish (v1 hard constraint);
  "hey ultron" is a fixed acoustic token spoken the way the owner says it — only its sound
  matters. No English or multilingual phrases are recognized, ever.
- Command registry: owner-defined and resolved (Q-001) — 10 car actions + CP, each accepting the
  bare action name and the "enciende" variant; CP is *"cancelar parqueo"*. Lives in
  `model/registry.json` (D-005). Longest phrase ("enciende limpia parabrisas baja") fits the
  2 s analysis window.
- Primary speaker: the owner. Primary room: the deployment room. Mics: XIAO built-in PDM; Pi
  owner-supplied USB microphone.
- Devices: Seeed XIAO ESP32S3 Sense vs Orange Pi Zero 2W — one pipeline deployed to both; the
  benchmark decides the winner (D-001).
- PyTorch is the source of truth; Edge Impulse is one-way export/archive (D-003).
- Reproducibility (NFR-006): any reported result reproducible from seed (42) + dataset counts +
  pinned tool versions recorded in the **PR description** (`CHANGELOG.md` is generated from PR
  merges, never hand-edited).
- Dev host: macOS arm64; Pi OS: Debian 12 aarch64. Edge Impulse account + API key already
  available (lampara-aliz workflow).

## User Stories

> **Amendment (2026-11-19, owner-approved):** US-002 (device WAV recorders) moved to
> [spec 003](../003-can-act/) as T16 — device recording is not required until the hardware
> milestone. Milestone 1 runs on owner-supplied external recordings (`mic=ext`).

### US-008 — Validated dataset from existing recordings
**As a** device owner
**I want to** drop my existing recordings (phone/PC) into the dataset
**so that** training can start before the devices exist.

**Acceptance criteria:**
- **AC1:** `ultron dataset ingest` copies and labels external WAVs with the mic tag `ext`
  (FR-020).
- **AC2:** `ultron dataset validate` passes every clip or exits non-zero listing failures
  (FR-021).

## Functional Requirements (EARS, MoSCoW)

**FR-016 (Must)** — THE SYSTEM SHALL support a registry of up to 10 action commands plus a
cancel command (CP), plus one wake class and noise — v1 hard cap: 13 model classes.

**FR-020 (Must)** — WHEN `dataset ingest` runs, THE SYSTEM SHALL copy and label WAVs from ANY
source — device recordings **and** owner-supplied external recordings (e.g. phone/PC) — tagging
the mic source in the filename (`lm_xiao_01.wav` / `lm_ext_01.wav`).

**FR-021 (Must)** — WHEN `dataset validate` runs, THE SYSTEM SHALL check every clip is
16 kHz/16-bit/mono and exit non-zero listing each failing file.

**FR-022 (Must)** — WHEN features are computed, THE SYSTEM SHALL use DC-offset-corrected clips
(`dataset dcfix`); any NaN feature row SHALL fail the pipeline loudly — no silent training on
corrupt data (Q-007, lampara lesson).

> **DC offset, in plain language:** a microphone can add a constant voltage to every sample it
> records, shifting the whole waveform up or down away from zero. `dataset dcfix` subtracts that
> constant so the recording is recentered before feature extraction — otherwise every MFCC
> feature is computed on a skewed signal.

## Non-Functional Requirements

**NFR-005 (Data volume)** — ≥ 20 original recordings per accepted phrase variant (2 variants per
action command, 1 for CP → 21 variants; wake and noise ≥ 30 each), plus ≥ 4 seeded augmentations
per original; hold-out ≥ 8 originals per variant. Batch-recorded 10 s clips split per utterance
count toward the totals.

**NFR-006 (Reproducibility)** — any reported result reproducible from seed (42) + dataset counts +
pinned tool versions recorded in the **PR description** (CHANGELOG.md is generated from PR
merges, never hand-edited).

**NFR-007 (Parity, DSP clause)** — DSP per-frame parity vs reference vectors ≤ 1e-3 max abs diff
(float). The export clause of NFR-007 lives in spec 002.

## Implementation FAQ

**Q-003: Which mic on the Pi? What if none is present?**
A: Owner-supplied USB mic; tooling fails fast listing detected capture devices and exits. No
fallback to "assume device 0". (The runtime applies the same rule — spec 003.)

**Q-007: What happens on DC offset / NaN features?**
A: `dataset dcfix` corrects DC offset before features; any NaN feature row fails the pipeline
loudly (no silent training on corrupt data — lampara lesson).

## Success Metrics

- **Milestone 1 — "can hear"**: external dataset validated (US-008) + DSP parity green
  (NFR-007 DSP clause); recorders deferred to spec 003 (T16).

## Risks

- **Mic domain mismatch** (external/USB/PDM mics sound different → model biased to the
  recording mic): v1 trains on external audio; T16 (spec 003) adds device-mic recordings for a
  later retrain; the bench (spec 004) measures per-device accuracy.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-016/020–022, NFR-005/006/007 (DSP clause), and
the shared system context in its own words. If anything is ambiguous, STOP and ask.
