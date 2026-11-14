# Requirements — 005 Archived (Milestone M5)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (data pipeline + shared
> system context), [002-can-think](../002-can-think/) (model artifacts),
> [003-can-act](../003-can-act/) (devices + contracts), [004-can-decide](../004-can-decide/)
> (bench). FR/NFR IDs are **global** across all milestone specs.

## Overview

M5 closes the loop: one command uploads the dataset and the current model artifacts to a dedicated
Edge Impulse project as a one-way archive, and the release bookkeeping lands (docs complete,
CHANGELOG verified as PR-generated). Local artifacts are byte-identical before and after — the
sync never downloads or overwrites anything (AGENTS rule 3).

## Goals

1. `ultron ei sync` archives dataset + model to EI without touching local files.
2. All documentation complete and every link resolving; conventional-PR discipline verified.

## Non-Goals

- No inference from EI, no round-trip, no EI-triggered training — strictly export/archive.
- No new device or model work here.

## User Stories

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

## Success Metrics

- **M5 "archived"**: EI sync verified one-way (FR-012); docs complete.

## Risks

- **EI API drift**: the sync is mocked in tests; a real run is part of acceptance (US-006 AC1).

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates FR-012, the one-way rule (AGENTS rule 3), and the
shared system context (spec 001) in its own words. If anything is ambiguous, STOP and ask.
