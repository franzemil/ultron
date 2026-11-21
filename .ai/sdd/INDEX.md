# Spec Index

> Dashboard only — the per-spec `.status` file is the single source of truth.
> Feature numbers come from the filesystem: highest prefix in `specs/` + 1.
> Milestones 1–5 sequence the work ([PLAN.md](../PLAN.md)); FR/NFR IDs are **global**
> across all milestone specs.

| Spec | Title | Milestone | Status |
|---|---|---|---|
| [001](specs/001-can-hear/requirements.md) | Can Hear — CLI, dataset tooling, recorder utilities, MFCC parity | Milestone 1 — Can hear | `tasks:approved` |
| [002](specs/002-can-think/requirements.md) | Can Think — features, subset training (la+lb), host TFLite test, export + parity gate | Milestone 2 — Can think | `tasks:approved` |
| [003](specs/003-can-act/requirements.md) | Can Act — staged: deploy → relays → per-command rollout — firmware + Pi runtime, SuperMini relays, audio confirmations, device recording | Milestone 3 — Can act | `requirements:draft` |
| [004](specs/004-can-decide/requirements.md) | Can Decide — benchmark + device decision (runs last) | Milestone 4 — Can decide | `requirements:draft` |
| [005](specs/005-archived/requirements.md) | Archived — EI one-way sync, docs + release | Milestone 5 — Archived | `requirements:draft` |

Approval order follows the milestones: approve 001 → implement Milestone 1 while reviewing 002,
and so on. **No open questions remain system-wide** (Q-011 resolved 2026-11-19: fixed-time
wipers; the on-device US-002 recording pass moved from 001 to 003 as T28 — owner decision
2026-11-19). Task-level dependencies and the derived critical path:
[CRITICAL-PATH.md](CRITICAL-PATH.md).
