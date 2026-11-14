# Design — 005 Archived (Milestone M5)

## Executive summary

`ultron ei sync` uploads `dataset/originals/**` with labels, `model/registry.json` (label
metadata), and the current `model_int8.tflite` to the configured EI project, then proves — via
checksum comparison — that every local file is untouched. Release bookkeeping (docs, review
evidence, CHANGELOG discipline) completes the system.

## Requirements mapping

| FR / NFR | Design section |
|---|---|
| FR-012 | [EI sync](#ei-sync-one-way-archive) |
| NFR-006 | [Conventions](../../../.ai/steering/conventions.md) (seeds, changelog), T14 |

## EI sync (one-way archive)

`ultron ei sync` (FR-012, Should): uploads `dataset/originals/**` with labels, `registry.json`
(label metadata), and the current `model_int8.tflite` to the EI project from `.env` (Q-009).
Post-sync checksum comparison proves local files untouched; the command refuses to ever *download
or overwrite* local artifacts (AGENTS rule 3). Dry-run mode prints the exact target list before
any upload.

## Verification strategy

| Level | What | How |
|---|---|---|
| Unit | Sync logic | `tests/test_ei_sync.py` with mocked EI API (FR-012, dry-run) |
| Acceptance | EI sync | sync → checksum compare → EI project shows dataset + model |

## Release bookkeeping (T14)

README final; `docs/model.md`, `docs/retraining.md`, `docs/troubleshooting.md`; CHANGELOG
verified as PR-generated (no hand edits); `review.md` written with evidence; `.status` bumped per
phase in every spec.
