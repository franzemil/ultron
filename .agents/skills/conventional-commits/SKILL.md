---
name: conventional-commits
description: >-
  Write commit messages and PR titles following Conventional Commits with
  ultron-specific types and scopes (model, cli, dsp, esp32, orangepi, dataset,
  ei, bench, spec). Use whenever committing, creating PRs, amending messages,
  or reviewing git history in this repo.
license: MIT
---

# Conventional Commits — ultron

Adapted from conventional-changelog's `conventional-commit-message` skill and
dgalarza/agent-skills (both MIT); the ultron-specific types, scopes, and
examples below override generic defaults.

## Format

```
<type>(<scope>): <imperative summary, ≤ 72 chars, no trailing period>

[optional body — what and why, not how]

[optional footers — BREAKING CHANGE:, Fixes #123]
```

- Imperative mood, present tense: "add", not "added" or "adds".
- One logical change per commit; the repo must work after every commit.
- Never stage secrets or data (`.env`, `dataset/`, `build/` — AGENTS rule 2).
- No AI-attribution trailers (no `Co-Authored-By` bot lines).

## Types (changelog impact decides)

| Type | In changelog? | Use for |
|---|---|---|
| `feat` | yes | new capability (device, model, CLI) |
| `fix` | yes | bug fixes |
| `perf` | yes | measurable improvements (e.g. inference latency) |
| `revert` | yes | reverts (`revert: <original subject>`) |
| `docs` | no | documentation only |
| `refactor` | no | no behavior change |
| `test` | no | tests only |
| `ci` | no | workflows / build config |
| `chore` | no | tooling, deps, maintenance |
| `dataset` | yes* | recording/ingest/augment changes — *inference-affecting |

## Scopes (match the repo layout)

`model` · `cli` · `dsp` · `esp32` · `orangepi` · `dataset` · `ei` · `bench` · `spec`

Scope is optional for repo-wide changes (`dataset: …`, `bench: …` read naturally
without one).

## Concrete examples

```
feat(model): add per-class threshold sweep
fix(esp32): guard PDM ring-buffer overflow on audio task starvation
feat(pi): libgpiod output controller with 50 ms tick
feat(pi): fail fast when no capture device is found
feat(dsp): shared C MFCC with EI-parity vectors
feat(cli): dataset ingest accepts external recordings (mic tag ext)
dataset: record 24 originals per guiñador phrase (xiao + ext)
feat(ei): one-way dataset + model sync
bench: add 30-minute false-accept counter
fix(bench): parse EVT lines from squashed serial logs
docs(spec): resolve Q-011 wiper stop behavior
spec: approve requirements gate
ci(changelog): auto-append merged PR entries
chore(uv): pin torch 2.x.x for reproducible training
revert: feat(pi): libgpiod output controller

feat(model)!: replace 1 s window with 2 s window

BREAKING CHANGE: all deployed models must be retrained and redeployed.
```

**Ultron breaking-change convention:** any `model!`, `dataset!`, or `dsp!`
change means deployed models are invalidated — the `BREAKING CHANGE:` footer
must state that retraining + redeployment is required.

## Branch naming

`<type>/<short-description>` — e.g. `feat/output-controller`, `fix/pdm-overflow`.

## Inference-affecting PR body (NFR-006)

If a PR touches dataset, DSP, model, thresholds, conversion, or the device
inference path, its description must include: dataset counts per label, seed /
split hash, pinned versions (torch, onnx, onnx2tf, tensorflow, python), fresh
hold-out metrics, and the parity-report result.

## Changelog-bot commits

Only `.github/workflows/changelog.yml` writes to `CHANGELOG.md`, always as:
`docs(changelog): update [skip changelog]` — never hand-edit the file.

## Validation checklist

- [ ] Type is in the table and matches intent (`feat`/`fix` land in the changelog)
- [ ] Scope is from the ultron list (or omitted for repo-wide)
- [ ] Subject: imperative, ≤ 72 chars, no trailing period
- [ ] Breaking change → `!` + `BREAKING CHANGE:` footer (+ retraining note)
- [ ] Inference-affecting → PR body has NFR-006 metadata
- [ ] Nothing from `.env`, `dataset/`, `build/` staged
