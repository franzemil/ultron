# Tasks — 006 Can Act Full (draft stub)

## Requirement coverage

Filled at drafting time: FR-038, the full Actuation Behavior Contract channels, NFR-009 (full),
and the rollout clauses (FR-013; FR-032 spec 002).

## Implementation readiness check

- [ ] Spec 003 delivered (XIAO runtime, audio feedback, 2 direct relays)
- [ ] Owner's full recording set available
- [ ] This spec drafted and approved (requirements → design → tasks)

## Reserved tasks (moved from spec 003, IDs preserved where possible)

- **T16–T24 — rollout cycles** — one command per cycle (record → train → validate → export →
  redeploy), registry order or owner-chosen.
- **T32+ — companion stack** — SuperMini firmware (EVT parser + behavior engines + 9 relays +
  boot-safe), XIAO UART forwarder, wiring per the preserved schema, timing verification.

## Traceability note

Every task maps to requirements at drafting time; any FR without a task is a spec gap.
