# Requirements — 005 Pi + Archived (Milestone 5, final phase)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context), [002-can-think](../002-can-think/) (model artifacts),
> [003-can-act](../003-can-act/) (XIAO runtime + contracts), [004-can-decide](../004-can-decide/)
> (bench), [006-can-act-full](../006-can-act-full/) (companion stack + rollout). FR/NFR IDs are
> **global** across all milestone specs.

## Overview

Milestone 5 is the **final phase** (owner decision 2026-11-21): it brings up the **Orange Pi
Zero 2W runtime** on the already-proven pipeline, verifies cross-target parity (same flatbuffer,
same event semantics, same timing), then closes the loop: one command uploads the dataset and the
current model artifacts to a dedicated Edge Impulse project as a one-way archive, and the release
bookkeeping lands (docs complete, CHANGELOG verified as PR-generated). Local artifacts are
byte-identical before and after — the sync never downloads or overwrites anything (AGENTS rule 3).
By this milestone the full phrase set is rolled out (spec 006 rollout), so the archived model is
the complete artifact.

## Goals

1. The Orange Pi runs the same model and the same event/actuation semantics as the XIAO stack.
2. Cross-target parity is measured, not assumed (feedback + actuation timing within tolerances).
3. `ultron ei sync` archives dataset + model to EI without touching local files.
4. All documentation complete and every link resolving; conventional-PR discipline verified.

## Non-Goals

- No inference from EI, no round-trip, no EI-triggered training — strictly export/archive.
- No new model training here (specs 001–002); no new XIAO firmware unless a parity bug requires it.
- No companion-stack work (spec 006).

## User Stories

### US-004 — Deploy the identical model to both devices
**As a** device owner
**I want to** run the same exported model and semantics on the XIAO and the Pi
**so that** the benchmark compares devices, not models.

**Acceptance criteria:**
- **AC1:** Both devices load the same `.tflite` (int8 on XIAO; int8/float16 on Pi) — SHA-256 of
  the flatbuffer matches.
- **AC2:** Both devices emit identical event types/semantics for the same audio, and feedback +
  actuation timing stay within the FR-037/NFR-010 tolerances.

### US-002 (Pi clause) — Collect training data on the Pi
**As a** device owner
**I want to** record WAV clips from the Pi microphone
**so that** the model can also train on Pi-mic audio.
*(Moved here from spec 003 with the Pi runtime — owner decision 2026-11-21.)*

**Acceptance criteria:**
- **AC1:** The Pi recorder captures 16 kHz/16-bit/mono WAVs with the mic source tagged.
- **AC2:** Every recording passes `ultron dataset validate`.

### US-006 — Archive to Edge Impulse
**As a** device owner
**I want to** sync the dataset and current model to Edge Impulse
**so that** the work is archived on the platform from the previous project.

**Acceptance criteria:**
- **AC1:** `ultron ei sync` uploads dataset + model artifacts to the configured EI project.
- **AC2:** Local files are byte-identical before and after the sync (one-way).

## Functional Requirements (EARS, MoSCoW)

**FR-012 (Should)** — WHEN `ultron ei sync` is invoked, THE SYSTEM SHALL upload the dataset and
current model artifacts to the configured Edge Impulse project without modifying any local
artifact.

## Non-Functional Requirements

**NFR-006 (Reproducibility)** — any reported result reproducible from seed (42) + dataset counts +
pinned tool versions recorded in the **PR description** (CHANGELOG.md is generated from PR
merges, never hand-edited).

## Implementation FAQ

**Q-009: Which Edge Impulse project receives the sync?**
A: A dedicated EI project for ultron (not the lampara project); `EI_KEY` and project id in `.env`.

## Constraints (this milestone)

- Pi hardware (owner-supplied): Orange Pi Zero 2W, USB microphone, USB audio dongle + powered
  speaker, 3.3 V relay board (libgpiod).
- The Pi consumes the **same** exported artifacts and the **same** committed clips as the XIAO
  stack; no Pi-only assets or synthesis.

## Success Metrics

- **Milestone 5 — final phase**: Pi runtime passes the US-001 script with parity to the XIAO
  stack (US-004); EI sync verified one-way (FR-012); docs complete.

## Risks

- **EI API drift**: the sync is mocked in tests; a real run is part of acceptance (US-006 AC1).

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-012, the one-way rule (AGENTS rule 3), US-004's
parity clauses, and the shared system context (spec 001) in its own words. If anything is
ambiguous, STOP and ask.
