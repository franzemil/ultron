# Tasks — 005 Archived (Milestone 5)

## Requirement coverage

| Requirement | Tasks |
|---|---|
| FR-012, US-006 | T13 |
| NFR-006 | T13, T14 |

This spec **is** Milestone 5 (archived) — see [.ai/sdd/PLAN.md](../../PLAN.md). Depends on spec
001 (dataset + registry) and spec 002 (int8 artifact).

## Implementation readiness check

- [ ] `.status` reads `requirements:approved` (currently `requirements:draft`)
- [ ] `.status` reads `design:approved`
- [ ] `.status` reads `tasks:approved` — **gates all implementation work**
- [ ] Specs 001/002 delivered: dataset, `registry.json`, `model_int8.tflite`

## Tasks

### T13 — Edge Impulse one-way sync (2 h)
- Covers: FR-012, US-006. Deps: spec 001 T3, spec 002 T9. Priority: Should.
- Checklist: `ultron ei sync` (EI API via `.env` key; upload originals with labels + registry
  metadata + current int8 model), dry-run mode, post-sync local checksum comparison, refuses any
  download/overwrite of local artifacts.
- Likely files: `cli/src/ultron/ei_sync.py`, `tests/test_ei_sync.py` (mocked API).
- Verify: dry-run prints target list; real run uploads to EI project; local checksums identical.

### T14 — Docs + release bookkeeping (2 h)
- Covers: NFR-006, US-005 (doc completeness clause). Deps: all milestone specs. Priority: Should.
- Checklist: README final; `docs/model.md`, `docs/retraining.md`, `docs/troubleshooting.md`;
  CHANGELOG verified as PR-generated (no hand edits); `review.md` written with evidence; bump
  `.status` per phase in every spec.
- Verify: all doc links resolve; every inference-affecting change has a conventional PR title.

## Traceability note

Every task maps to requirements in the coverage table; any FR without a task is a spec gap —
stop and update the spec (never silently implement).
