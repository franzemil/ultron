# Requirements — 006 Can Act Full (Companion stack + rollout)

> Part of the ultron system. Siblings: [001-can-hear](../001-can-hear/) (shared system context),
> [002-can-think](../002-can-think/) (model artifacts), [003-can-act](../003-can-act/) (XIAO
> runtime), [004-can-decide](../004-can-decide/) (bench), [005-archived](../005-archived/) (Pi +
> archive, final phase). FR/NFR IDs are **global** across all milestone specs.

> **Stub (owner decision 2026-11-21):** scope reserved here after the XIAO-only split of spec
> 003. Full drafting (requirements → design → tasks) starts when the owner's full recording set
> is ready and spec 003 is delivered.

## Overview

Milestone 6 completes the XIAO actuation stack: the **companion ESP32-S3 SuperMini** drives all
**9 relay channels** (LM LB LA GD GI LPB LPA A CC) over the same `EVT` contract, and the
**per-command rollout** grows the recognized set from the minimal 4 classes (`wake`, `noise`,
`la`, `lb`) to the full registry — one record → train → validate → export → redeploy cycle per
command.

## Scope moved here (owner decision 2026-11-21)

- **FR-038** — companion ESP32-S3 SuperMini drives the 9 relays over UART (ID reserved in spec
  003; implementation here).
- **Full 9-channel Actuation Behavior Contract** — CP/parqueo, wipers (`motor_45s`), arranque
  (`pulse_2s`), cierre centralizado (`pulse_05s`).
- **NFR-009 full scope** — arranque safety activates with channel A.
- **Rollout cycles** (former spec 003 T16–T24) — one command per cycle, registry order or
  owner-chosen.

## Non-Goals

- No Pi work (spec 005, final phase).
- No new XIAO voice/state-machine work (spec 003 owns it) — this spec consumes its runtime.
- No WiFi/remote control.

## Implementation FAQ

**Q-015: When does drafting start? (open)**
A: After the owner provides the full recording set and spec 003 is delivered. This stub records
the reserved scope so nothing is lost.

## Confirm before building

No implementation before `tasks:approved`. Drafting of this spec starts after spec 003 completes.
