# Tasks — 004 Can Decide (Milestone M4)

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-014, NFR-004 | T12 |
| FR-008 / FR-009 / NFR-010 (verification clause) | T12 |
| NFR-001, NFR-002, NFR-008 (bench clauses) | T12 |
| US-005 | T12 |

Milestone M4 = phase P6 of [.ai/sdd/PLAN.md](../../PLAN.md). Depends on spec 003 (both devices
running the loop end-to-end).

## Implementation readiness check

- [ ] `.status` reads `requirements:approved` (currently `requirements:draft`)
- [ ] `.status` reads `design:approved`
- [ ] `.status` reads `tasks:approved` — **gates all implementation work**
- [ ] Spec 003 delivered: both devices pass US-001 AC1–AC3 (incl. "Escuchando" feedback)

## Tasks

### T12 — Benchmark harness + device decision (3 h)
- Covers: FR-014, NFR-004, US-005. Deps: spec 003 T10b, T11. Priority: Must.
- Checklist: `ultron bench run --device xiao|pi` parses event + output logs; latency, confusion
  matrix (incl. luz triple and guiñador pair per Q-002), 30 min false-accept counter, memory
  stats, output timing checks (FR-009 tolerances) + feedback timing (NFR-010); writes
  `docs/bench.md` table; device decision block with justification.
- Likely files: `cli/src/ultron/bench.py`, `docs/bench.md`.
- Verify: bench table populated for both devices from real logs; NFR-004 targets evaluated.

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).
