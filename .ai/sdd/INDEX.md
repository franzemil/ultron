# Spec Index

> Dashboard only — the per-spec `.status` file is the single source of truth.
> Feature numbers come from the filesystem: highest prefix in `specs/` + 1.
> Milestones 1–5 sequence the work ([PLAN.md](../PLAN.md)); FR/NFR IDs are **global**
> across all milestone specs.

| Spec | Title | Milestone | Status |
|---|---|---|---|
| [001](specs/001-can-hear/requirements.md) | Can Hear — CLI, dataset tooling, MFCC parity (recorders moved to 003) | Milestone 1 — Can hear | `design:approved` |
| [002](specs/002-can-think/requirements.md) | Can Think — features, PyTorch training, TFLite export + parity gate | Milestone 2 — Can think | `requirements:draft` |
| [003](specs/003-can-act/requirements.md) | Can Act — firmware + Pi runtime, SuperMini relays, audio confirmations, recorders | Milestone 3 — Can act | `requirements:draft` |
| [004](specs/004-can-decide/requirements.md) | Can Decide — benchmark + device decision | Milestone 4 — Can decide | `requirements:draft` |
| [005](specs/005-archived/requirements.md) | Archived — EI one-way sync, docs + release | Milestone 5 — Archived | `requirements:draft` |

Approval order follows the milestones: approve 001 → implement Milestone 1 while reviewing 002,
and so on. **No open questions remain system-wide** (Q-011 resolved 2026-11-19: fixed-time
wipers; US-002/T16 device recorders moved from 001 to 003 — owner decision 2026-11-19).
