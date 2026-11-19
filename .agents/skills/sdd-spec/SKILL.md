---
name: sdd-spec
description: >-
  Use when drafting, reviewing, or amending anything in .ai/sdd/specs/ —
  enforces the ultron requirements system: user stories with acceptance
  criteria, EARS functional requirements, MoSCoW, per-milestone spec folders
  with .status gates, and FR→task traceability. Covers requirements.md,
  design.md, and tasks.md conventions.
license: MIT
---

# SDD Spec Writing — ultron

The system every ultron spec follows, distilled from the five milestone specs
(`001-can-hear` … `005-archived`). The golden rule: **requirements are testable
statements, not prose** — user stories carry acceptance criteria, functional
requirements use EARS syntax, and every requirement traces to a task.

## When to use

- Drafting a new spec folder (`specs/NNN-name/`) or amending an existing one.
- Reviewing a spec draft before a gate flip (`requirements:approved`, …).
- Deciding where a statement belongs (requirement vs design vs task).

Not for: commit messages/PRs (use `conventional-commits`), implementation code.

## Before touching a spec

1. Read the spec's `.status` file — it is the single source of truth for gates.
   File existence implies nothing; only `*:approved` unlocks the next phase.
2. Phase order is fixed: requirements → design → tasks, each gated by explicit
   human approval. Never write implementation code before `tasks:approved`.
3. Ambiguity → **STOP and ask the owner**. Never resolve owner decisions
   (phrases, hardware, behaviors) by assumption.
4. FR/NFR IDs are **global across all milestone specs** (FR-001…FR-022,
   NFR-001…NFR-010). Never renumber; new requirements take the next free ID.

## requirements.md — required section order

```text
# Requirements — NNN Name (Milestone N)
> cross-references to sibling specs + "FR/NFR IDs are global" note
## Overview            # what this milestone builds, in plain language
## Goals               # numbered, outcome-focused
## Non-Goals           # explicit exclusions, each with a one-line reason
## User Stories        # US-001… with acceptance criteria (see below)
## Functional Requirements (EARS, MoSCoW)   # FR-xxx (Must|Should)
## <Contract sections if any>               # e.g. Actuation Behavior Contract
## Non-Functional Requirements              # NFR-xxx (Latency, Memory, …)
## Constraints (this milestone)             # hardware, language, environment
## Decisions                                # D-xxx with one-line rationale
## Implementation FAQ                       # Q-xxx: Resolved / (open — resolve before approval)
## Success Metrics                          # the milestone exit criterion
## Risks                                    # risk → mitigation
## Confirm before building                  # restatement duty + open-question gates
```

A shared "Shared system context" section (constraints + global decisions that
predate the split) lives in spec 001; sibling specs reference it, never copy it.

## User stories & acceptance criteria

```markdown
### US-00N — Title
**As a** device owner
**I want to** <capability>
**so that** <value>.

**Acceptance criteria:**
- **AC1:** <observable, testable outcome>
```

- Acceptance criteria **belong to requirements** — never in design.md or
  tasks.md. Every US has ≥ 1 AC; every AC is observable (an event, a file, an
  exit code, a measured timing) and maps to a task's Verify line.
- ACs may reference FRs (e.g. "…(FR-006)") to keep traceability tight.

## EARS — the only allowed functional-requirement patterns

```
WHEN <trigger>, THE SYSTEM SHALL <response>.        # event-driven
IF <condition>, THE SYSTEM SHALL <response>.        # conditional / fallback
WHILE <state>, THE SYSTEM SHALL <behavior>.         # ongoing invariant
WHERE <configuration>, THE SYSTEM SHALL <behavior>. # optional feature
THE SYSTEM SHALL <invariant>.                       # unconditional
```

Rules:

- One behavior per FR. No "and" joining two unrelated behaviors.
- System voice always: **THE SYSTEM SHALL** — never "the app should try to".
- Tag every FR with MoSCoW: `**FR-xxx (Must)**` or `**FR-xxx (Should)**`.
- Quantify everything testable: timings (≤ 200 ms), sizes (≤ 100 KB), rates
  (≥ 90%), tolerances (±50 ms). "Fast" / "small" / "robust" are not spec words.
- NFRs get a category label: `**NFR-xxx (Latency)**`, `(Memory)`, `(Parity)`…

## House style

- Gloss jargon in plain language at first use, set off in the text (see
  "Governance" in 001 tasks and "npz" in 002 tasks — the pattern to copy).
- Decisions carry a one-line italic rationale: `*(Owner wants measured
  comparison rather than assuming the MCU is enough.)*`
- Open questions are explicit: `**Q-xxx: …? (open — resolve before approval)**`.
  Resolved questions start their answer with `A: **Resolved.**`
- Contracts (event, actuation) live in the spec that implements them (003);
  other specs reference, never duplicate.
- Cross-spec pointers are relative links; `model/registry.json` stays the
  single source of truth for phrases/behaviors — specs describe, not mirror.

## Traceability (tasks.md)

- Every FR, NFR, and US of the spec appears in the coverage table
  (`| Requirement | Tasks |`). **An FR without a task is a spec gap — stop and
  update the spec, never silently implement.**
- Each task: `Covers: …`, `Deps: …`, `Priority: Must|Should`, `Checklist`,
  `Likely files`, `Verify:` — the Verify line must be a runnable command or an
  on-device script, and must echo an AC or FR clause.
- Express each task **SMART**: **S**pecific — scope plus explicit exclusions;
  **M**easurable — the runnable Verify with quantified tolerances; **A**chievable
  — deps, inputs, and why it is feasible now; **R**elevant — the FR/NFR/US
  mapping and what it feeds next; **T**ime-bound — estimate with a session-level
  time breakdown.
- Readiness check block lists the gates plus any spec-specific open question
  that still gates approval. The skill never records resolved-decision history —
  decisions live in the spec that owns them.

## Canonical examples

- `specs/001-can-hear/` — shared system context + data-tooling requirements.
- `specs/003-can-act/` — the fullest example: contracts, timing budgets,
  open-question gating.
- `specs/004-can-decide/` — verification-clause requirements (defines what is
  *measured*, implemented elsewhere).

## Drafting checklist

- [ ] `.status` read first; phase respected
- [ ] All sections present, in order
- [ ] Every US has testable ACs; ACs live in requirements.md only
- [ ] Every FR uses an allowed EARS pattern + MoSCoW tag
- [ ] Every testable statement quantified (no adjectives)
- [ ] New IDs continue the global FR/NFR sequences
- [ ] Open questions marked; owner decisions not assumed
- [ ] Coverage table complete; every requirement has a task with Verify

## Review checklist (before flipping a gate)

- [ ] Readiness-check items all satisfied for the gate being flipped
- [ ] No `(open)` questions remain in the spec being approved
- [ ] Cross-references to sibling specs resolve
- [ ] Gates flipped in order; `.status` updated in the same PR
