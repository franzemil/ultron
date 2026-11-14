# Requirements — NNN Name (Milestone N)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/),
> [002-can-think](../002-can-think/), [003-can-act](../003-can-act/),
> [004-can-decide](../004-can-decide/), [005-archived](../005-archived/) —
> keep only the real siblings. FR/NFR IDs are **global** across all milestone
> specs; shared system context lives in [spec 001](../001-can-hear/).

## Overview

<What this milestone builds, in plain language. Two to five sentences.>

## Goals

1. <Outcome-focused goal>
2. <Outcome-focused goal>

## Non-Goals

- **No <exclusion>** — <one-line reason>.

## User Stories

### US-00N — <Title>
**As a** device owner
**I want to** <capability>
**so that** <value>.

**Acceptance criteria:**
- **AC1:** <observable, testable outcome — an event, a file, an exit code, a timing>

## Functional Requirements (EARS, MoSCoW)

**FR-0XX (Must)** — WHEN <trigger>, THE SYSTEM SHALL <response>.

**FR-0XX (Must)** — THE SYSTEM SHALL <invariant, quantified>.

## Non-Functional Requirements

**NFR-0XX (Category)** — <quantified constraint>.

## Constraints (this milestone)

- <hardware / language / environment constraints>

## Decisions

- **D-0XX** — <decision>. *(<one-line rationale>)*

## Implementation FAQ

**Q-0XX: <question>?**
A: **Resolved.** <answer> — or mark the question `(open — resolve before approval)`.

## Success Metrics

- **Milestone N — "<name>"**: <the exit criterion from PLAN.md>.

## Risks

- **<Risk>** → <mitigation>.

## Confirm before building

No implementation code until this spec's `.status` reads `tasks:approved`. Before starting
implementation the implementing agent restates <the FR ranges, contracts, and decisions> in its
own words. If any acceptance criterion is ambiguous, ask before implementing.
