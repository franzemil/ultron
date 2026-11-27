# Ultron — Product Plan

Medium project: one spec folder per milestone. Milestones below sequence the work; every
milestone ends at a gate (`.status`) before the next begins. Governance (steering docs, specs
drafted and approved) gates every milestone start.

## Feature map

| # | Feature (milestone) | Spec |
|---|---|---|
| 001 | Can Hear — CLI, dataset tooling, recorder utilities, MFCC parity (Milestone 1) | `specs/001-can-hear/` |
| 002 | Can Think — features, subset training, host inference test, TFLite export + parity gate (Milestone 2) | `specs/002-can-think/` |
| 003 | Can Act — **XIAO only**: firmware, audio confirmations, 2 direct relays; staged console → audio → relays (Milestone 3) | `specs/003-can-act/` |
| 006 | Can Act Full — companion ESP32-S3 SuperMini 9 relays + per-command rollout (Milestone 6, runs after 003) | `specs/006-can-act-full/` |

**Execution order: 001 → 002 → 003 → 006.** (Owner decision 2026-11-26: Orange Pi removed — specs 004/005 deleted; the previous order was 001 → 002 → 003 → 006 → 004 → 005.)
Spec IDs are creation order; the milestone sequence above is the dependency order. The Orange Pi (removed 2026-11-26 — see the scope amendment in specs 001/003; historical text follows) 
runtime lives in the final phase (005); the companion ESP32-S3 SuperMini stack and the remaining
relay channels / command rollout live in 006.

Later features (WiFi control, multi-device, speaker enrollment) get their own spec folders.
Shared system context (constraints, D-decisions, global rules) lives in spec 001.

## Milestones (the plan)

| Milestone | Goal | Exit criterion | Spec |
|---|---|---|---|
| **Milestone 1 — "can hear"** | Data tooling: CLI scaffold, dataset ingest/validate/dcfix, recorder utilities (XIAO + Pi), augmentation; DSP: shared C MFCC + bindings + parity vectors vs EI reference | `ultron dataset validate` green on ≥ 30 originals/label (initial labels: `wake`, `noise`, `la`, `lb`; each rollout label is validated in its own Stage C cycle); per-frame parity test passes (max abs diff ≤ 1e-3); on-device validation deferred — optional pass (HW-1, D-012) | 001 |
| **Milestone 2 — "can think"** | Model: feature npz builder, PyTorch **subset** training (initial `wake noise la lb`, TD-012), threshold sweep; Export: ONNX → onnx2tf → TFLite int8/float16 + parity report; host `ultron infer` local test (files + live mic, both artifacts) | Fresh-hold-out metrics meet NFR-003; NFR-007 parity ≥ 99% argmax agreement; `ultron infer` green on both artifacts | 002 |
| **Milestone 3 — "can act" (XIAO)** | XIAO firmware: state machine, events, audio confirmations ("Escuchando" + "comando"), 2 relay channels direct (LM, LB), XIAO device recording — staged: **A** console-only → **B** audio + relays | A: contract events on the XIAO; B: `la`/`lb` actuate LM+LB; feedback timing meets NFR-010/FR-037; XIAO recordings valid (US-002) | 003 |
| ~~Milestone 4 — "can decide"~~ | *Withdrawn 2026-11-26: there is only one device, so there is nothing to decide. Latency/RAM are already measured in T12.* | — | — |
| **Milestone 6 — "can act full"** | Companion ESP32-S3 SuperMini drives all 9 relay channels over UART (FR-038); per-command rollout completes the registry | 9-channel contract timing verified; every command recognized + actuated | 006 |
| ~~Milestone 5 — final phase~~ | *Withdrawn 2026-11-26 with the Pi target.* | — | — |

## Rollout phases (owner-facing plan)

| Phase | Content | Where |
|---|---|---|
| **P1** | can hear — data tooling, recorder utilities, MFCC | 001 |
| **P2** | can think — 4-class model (`wake noise la lb`) + export + **local test on host (int8 + float16)** | 002 |
| **P3** | deploy — minimal model runs on the **XIAO**, inference + events + feedback | 003 Stage A |
| **P4** | relays — minimal model actuates 2 real channels (LM, LB) directly | 003 Stage B |
| **P5+** | full stack — companion SuperMini 9 relays + add commands one by one (record → train → validate → export → redeploy) | 006 |
| **P6** | benchmark the XIAO stack | 004 |
| ~~last~~ | *(withdrawn 2026-11-26 with the Pi target)* | — |

## Dependencies

Milestone 1 → 2 → 3 → 4 → 5. The firmware TFLM spike (T11, spec 002) runs **early and
parallel** inside Milestone 2 because it is the riskiest unknown (Arduino TFLM library
availability) and its outcome gates Milestone 3 planning. The can-decide benchmark (004) runs
**after** Stage C completes so the bench measures the full phrase set (owner decision). Per-task
dependencies and the derived critical path (≈ 52.5 h, rollout-dominated) live in
[CRITICAL-PATH.md](CRITICAL-PATH.md).

**Hardware logistics**: Milestone 1 closes on host-side criteria — external recordings satisfy
the data minimums (D-012), so the Pi USB microphone and XIAO are only needed for the deferred,
optional device pass (HW-1). The Pi USB microphone must be on hand before Milestone 3 (runtime
capture + device recording, T28); the MAX98357A I2S amp + speaker (XIAO, **path validated
2026-11-20**) and the USB audio dongle + powered speaker (Pi) must be on hand before Milestone 3
(T14, audio feedback); the 9-channel relay board + ESP32-S3 SuperMini must be on hand before
Milestone 3 (T15, relays).
