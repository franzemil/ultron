# Spec Index

> Dashboard only — the per-spec `.status` file is the single source of truth.
> Feature numbers come from the filesystem: highest prefix in `specs/` + 1.
> Milestones 1–5 sequence the work ([PLAN.md](../PLAN.md)); FR/NFR IDs are **global**
> across all milestone specs.

| Spec | Title | Milestone | Status |
|---|---|---|---|
| [001](specs/001-can-hear/requirements.md) | Can Hear — CLI, dataset tooling, recorder utilities, MFCC parity | Milestone 1 — Can hear | `tasks:approved` |
| [002](specs/002-can-think/requirements.md) | Can Think — features, subset training (la+lb), host TFLite test, export + parity gate | Milestone 2 — Can think | `tasks:approved` |
| [003](specs/003-can-act/requirements.md) | Can Act — XIAO only: staged console → audio → 2 direct relays | Milestone 3 — Can act | `requirements:draft` |
| [006](specs/006-can-act-full/requirements.md) | Can Act Full — companion SuperMini 9 relays + command rollout (stub) | Milestone 6 — Can act full | `requirements:draft` |
| [004](specs/004-can-decide/requirements.md) | Can Decide — XIAO benchmark (Pi comparison deferred to 005) | Milestone 4 — Can decide | `requirements:draft` |
| [005](specs/005-archived/requirements.md) | **Final phase** — Pi runtime + parity, EI sync, docs + release | Milestone 5 — Pi + archived | `requirements:draft` |

Approval order: 001 → 002 → 003 → 006 → 004 → 005 (owner decision 2026-11-21: Orange Pi in
the final phase; companion SuperMini stack + rollout in 006). **No open questions remain in any
approved spec** (Q-011 resolved 2026-11-19; Q-014 resolved 2026-11-21; US-002's Pi pass moved to
005). Task-level dependencies and the derived critical path:
[CRITICAL-PATH.md](CRITICAL-PATH.md).
