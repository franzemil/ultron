# Requirements — 004 Can Decide (Milestone 4)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context), [002-can-think](../002-can-think/) (model artifacts),
> [003-can-act](../003-can-act/) (devices + contracts — the event and actuation contracts this
> bench verifies live there), [005-archived](../005-archived/) (EI sync). FR/NFR IDs are
> **global** across all milestone specs.

## Overview

Milestone 4 measures, honestly and repeatably, the XIAO stack: latency, memory, false accepts,
per-command accuracy (including the known close pairs), output timing, and feedback timing — all
parsed from device logs, no manual transcription. The result is a completed `docs/bench.md` table
on the XIAO. **Owner decision 2026-11-21:** the Orange Pi runtime arrives in spec 005 (final
phase), so the cross-target comparison is deferred to spec 005's parity pass (T30); this
milestone validates the shipping XIAO stack after spec 006's full rollout.

## Goals

1. One command (`ultron bench run --device xiao`) produces the metrics row for the XIAO stack.
2. The XIAO is measured on a scripted protocol; the Pi comparison is deferred to spec 005 (T30).
3. The result is declared in `docs/bench.md` with the numbers that justify it.

## Non-Goals

- No new device behavior here (spec 003/006 owns the runtime); the bench only observes it.
- No training changes (specs 001–002) — the same model artifacts are used on both devices.
- No EI upload (spec 005).
- No Pi measurement or device-vs-device decision (spec 005, final phase — T30 parity).

## User Stories

### US-005 — Benchmark and pick the device
**As a** device owner
**I want to** run the comparison bench
**so that** the device choice is backed by measured data.

**Acceptance criteria:**
- **AC1:** `docs/bench.md` contains latency, memory, false-accepts, and per-command confusion
  across the full rolled-out phrase set for both devices on the same test protocol.
- **AC2:** The winner is declared in `docs/bench.md` with the numbers that justify it.

## Functional Requirements (EARS, MoSCoW)

**FR-014 (Should)** — WHILE ambient audio persists, THE SYSTEM SHALL count and report wake
false-accepts and command false-events for the bench report.

## Non-Functional Requirements

**NFR-004 (False accepts)** — during a 30-minute ambient bench: ≤ 1 false wake and 0 command
events while in wake-listening state.

*(Also verified here, defined elsewhere: NFR-001 latency, NFR-002 memory, FR-009 output
tolerances, NFR-010 feedback timing, Q-002 close pairs — all measured from the protocol below.)*

## Implementation FAQ

**Q-002: What about acoustically close commands (luz media/baja/alta; guiñador
derecho/izquierdo)?**
A: The bench measures those exact confusion pairs (threshold tuning and phrase revision follow
spec 002's mitigation order).

**Q-004: Both devices in the same room during bench?**
A: Bench runs sequentially; only one device is listening at a time to avoid cross-triggering.

## Success Metrics

- **Milestone 4 — "can decide"**: bench table complete; device winner declared with justification (US-005).

## Risks

- **Mic domain mismatch**: the bench reports per-device accuracy (data-side mitigation in spec
  001 — record from both mics, augment).
- **False-wake chatter with playback**: the false-accept run counts false wakes with the speaker
  live ("Escuchando" plays on each false wake — bounded by the NFR-004 target itself).

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-014, NFR-004, the bench protocol (design), and
the shared system context (spec 001) in its own words. If anything is ambiguous, STOP and ask.
