# Design — 004 Can Decide (Milestone 4)

## Executive summary

`ultron bench run --device xiao|pi` executes a scripted, sequential protocol against each device,
parsing the event contract lines (spec 003) plus relay/output debug lines into a metrics table.
Human operator only timestamps utterances; everything else is machine-parsed into
`docs/bench.md`, which ends with the device decision block.

## Requirements mapping

| FR / NFR | Design section |
|---|---|
| FR-014, NFR-004 | [Protocol](#protocol) item 3 |
| FR-008 (contract parse) | [Parsing](#parsing) |
| FR-009 (tolerances), NFR-010 | [Protocol](#protocol) item 5 |
| NFR-001, NFR-002 | [Protocol](#protocol) items 1 and 4 |
| NFR-008 (bench table) | [Output](#output) |

## Protocol

Scripted, sequential (Q-004), per device (FR-014, US-005). Precondition: the full phrase set is
rolled out (spec 003 Stage C complete).

1. **Latency**: 20 wake utterances → measure end-of-utterance → `WAKE` (via `t_ms` deltas +
   operator timestamping protocol in `docs/bench.md`).
2. **Accuracy**: each command of the full rolled-out set × 10 live utterances → confusion
   matrix from event log.
3. **False accepts**: 30 min ambient listening → count false `WAKE` (target ≤ 1) and false `CMD`
   (target 0) (NFR-004). Speaker stays live — a false wake plays "Escuchando", as it would in
   deployment.
4. **Memory**: reported arena/heap stats from device logs (NFR-002).
5. **Output timing**: relay channels probed during scripted commands — blink period 2.0 s
   ±200 ms, GD/GI span 45 s ±1 s, A pulse 2 s ±50 ms, CC pulse 0.5 s ±50 ms, CP stop < 200 ms
   (FR-009); feedback: "Escuchando" starts ≤ 200 ms after `WAKE` and ends ≤ 500 ms later
   (NFR-010).
6. Results table + device decision in `docs/bench.md`.

## Parsing

Consumes only contract lines — `EVT …` serial lines (XIAO) / JSON `event` lines (Pi) — plus
`DBG out=` and `DBG play=` lines (NFR-008) from captured logs. Never free-text device output.

## Output

`docs/bench.md`: one metrics section per device, a comparison table, and a **device decision**
block stating the winner with the numbers that justify it (US-005 AC2).

## Verification strategy

| Level | What | How |
|---|---|---|
| Integration | Bench harness | table populated for both devices from real logs; NFR-004 targets evaluated |
| Acceptance | Decision | winner declared with numbers in `docs/bench.md` |
