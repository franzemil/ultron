# Spec Index

> Dashboard only — the per-spec `.status` file is the single source of truth.
> Feature numbers come from the filesystem: highest prefix in `specs/` + 1.
> Milestones (M1→M5) and phases (P1→P7) live in [PLAN.md](../PLAN.md).
> FR/NFR IDs are **global** across all milestone specs.

| Spec | Title | Milestone | Phase | Status |
|---|---|---|---|---|
| [001](specs/001-can-hear/requirements.md) | Can Hear — CLI, dataset tooling, recorders, MFCC parity | M1 | requirements | `requirements:draft` |
| [002](specs/002-can-think/requirements.md) | Can Think — features, PyTorch training, TFLite export + parity gate | M2 | requirements | `requirements:draft` |
| [003](specs/003-can-act/requirements.md) | Can Act — firmware + Pi runtime, relays, "Escuchando" feedback | M3 | requirements | `requirements:draft` |
| [004](specs/004-can-decide/requirements.md) | Can Decide — benchmark + device decision | M4 | requirements | `requirements:draft` |
| [005](specs/005-archived/requirements.md) | Archived — EI one-way sync, docs + release | M5 | requirements | `requirements:draft` |

Approval order follows the milestones: approve 001 → implement M1 while reviewing 002, and so
on. Q-011 (wiper stop) is the only open question system-wide and gates **only spec 003**.
