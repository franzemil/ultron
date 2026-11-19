# A-001 — Three-way split: train / validate / test

> Amendment to [spec 001](../specs/001-can-hear/) (data layout, NFR-005) and
> [spec 002](../specs/002-can-think/) (NFR-003, design, tasks).
> FR/NFR IDs are **global** across all milestone specs; this amendment changes the
> meaning of two existing NFRs and adds one FR (FR-037).

| Field | Value |
|---|---|
| Kind | requirement amendment (not a new milestone) |
| Raised | 2026-11-18 |
| Raised by | implementation audit during T10 host-inference work |
| Gate | `amendment:A-001:proposed` — **no implementation until the owner approves §5** |
| Affects | `NFR-003` (002), `NFR-005` (001), new `FR-037` (002), both design docs, both task lists |
| Blocks | any further work on dataset splits, threshold sweep, training reports, and the rename in §9 |

## 1. Trigger — a measured conflict, not a style preference

NFR-003 certifies the hold-out as the honest accuracy number:

> **NFR-003 (Accuracy, fresh data)** — on a hold-out set never seen in
> training/augmentation: ≥ 90% accuracy per **trained** command label and ≥ 90% wake recall.

But the operating point is **selected on that same set**:

- `cli/src/ultron/train.py:182` → `thresholds, sweep_report = t8.run_sweep(ckpt, holdout)`
- spec 002 design:53 → "grid over threshold ∈ {0.6…0.95} × margin ∈ {0.05…0.20} **against the
  hold-out**, writing `model/thresholds.json` (Q-006)"

Any set used to choose a threshold stops being unseen evidence. After `train sweep` runs, the
"fresh hold-out" figure is a *validation* number, and the spec has no test set at all.

Measured on the owner's 41-clip dataset (2026-11-18), which is why this matters in practice:

| run | dataset | hold-out accuracy | note |
|---|---|---|---|
| 1 | 33 originals, no augmentation | **23.08%** | wake 0%, lb 0%, la 50%, noise 100% |
| 2 | + 132 augmented files → 199 windows | **84.62%** | wake 100%, la 100%, lb 85.7%, noise 50% |

Run 2's 84.62% is currently reported as the NFR-003 number, yet `train sweep` chose
0.95 / 0.20 by maximising recall **on those very 13 windows** — and printed
`WARNING: no grid point meets NFR-003`. Both statements cannot be true at once.

## 2. Vocabulary — "hold-out" is retired

The term currently names two different things in the same sentence, which is the root of the
confusion the owner hit. Three sets, three names, one owner each:

| Set | Location | What may read it | Standard name |
|---|---|---|---|
| train | `dataset/originals/`, `dataset/augmented/` | training loop only | **train** |
| selection | `dataset/validate/<label>/` | early stopping **and** threshold sweep | **validate** |
| evidence | `dataset/test/<label>/` | one grading pass, nothing else | **test** |

"hold-out" is removed from requirements, design, task text, CLI flags, report names and file
paths. The archive's internal 90/10 split (spec 002 design:50) is a *training-time* validation
split and is named `train_val` so it can never be confused with the validate set.

## 3. Decision D-A1 — isolation stays structural

*(Three sets, enforced by location, because a flag that can point anywhere is how this leak
happens; the tree already is the labelling in this system.)*

```
dataset/                          # gitignored — the tree IS the labeling
  originals/<label>/*.wav         # train
  augmented/<label>/aug_*.wav     # train (4 seeded variants per original, deterministic)
  validate/<label>/*.wav          # selection only — early stop + threshold sweep
  test/<label>/*.wav              # evidence only — graded once, after the operating point freezes
  takes/                          # long takes, never trained
```

Rules:

1. `ultron features` reads `originals/` + `augmented/` (+ `synthetic/` behind
   `--with-synthetic`, unchanged) and **never** `validate/`, `test/` or `takes/`.
2. `ultron train run` scores validate (selection context) and test (evidence) **separately**,
   in that order, and writes two reports (§6).
3. `ultron train sweep` reads **validate only**, and writes the operating point.
4. The test split is read exactly once per operating point. Re-running the test evaluation after
   changing anything derived from it (threshold, margin, class set, architecture) is a new
   operating point and must be disclosed in the PR description (NFR-006).

## 4. Data layout delta (spec 001 design:33–40)

Old:

```
  holdout/<label>/*.wav           # fresh recordings, NEVER trained on (NFR-003) — isolation is structural
```

New:

```
  validate/<label>/*.wav          # selection: early stopping + threshold sweep — never trained on
  test/<label>/*.wav              # evidence: graded once per frozen operating point — never selected on
```

The paragraph that says "`holdout/` isolation is enforced by *location*, never by a flag that
could silently leak data into training" keeps its reasoning and gains the selection rule: *the
test split is separated from the validate split by location too, because the sweep is a
selection step and would otherwise eat the evidence set.*

## 5. Requirement deltas (owner approval required)

### 5.1 NFR-003 — reworded, ID kept (spec 002 requirements:119)

Old:

> **NFR-003 (Accuracy, fresh data)** — on a hold-out set never seen in training/augmentation:
> ≥ 90% accuracy per **trained** command label and ≥ 90% wake recall. Val-split numbers are
> reported only as memorization context (lampara-aliz honesty rule).

New:

> **NFR-003 (Accuracy, evidence data)** — on the **test** set (`dataset/test/<label>/`) — never
> seen in training/augmentation and never used for any selection (early stopping, threshold sweep,
> margin, class set, architecture) — ≥ 90% accuracy per **trained** command label and ≥ 90% wake
> recall. A test result is admissible only when the operating point was frozen before the test set
> was read. Validate-set numbers are reported as selection context and train-split numbers as
> memorization context (lampara-aliz honesty rule); neither may be quoted as the accuracy claim.

### 5.2 NFR-005 — data volume for two reserved sets (spec 001 requirements:94)

Old: `hold-out ≥ 8 originals per variant.`

New: `validate ≥ 8 originals per variant and test ≥ 8 originals per variant (16 reserved
recordings per variant, counted from real recordings only — augmented/synthetic clips never
satisfy either minimum).`

*Rationale: reserving one set of 8 was affordable; two sets of 8 is the price of honest evidence.*

### 5.3 FR-037 (Must) — new, next free global ID

> **FR-037 (Must)** — IF a selection step (early stopping, threshold sweep, margin choice, class-set
> or architecture choice) is about to read `dataset/test/`, THE SYSTEM SHALL refuse that step and
> exit non-zero naming the offending split.

*Rationale: the leak in §1 was invisible in the spec and only visible in a call site. A refusal
makes reintroducing it a test failure instead of a silent metric inflation.*

Traceability: FR-037 → spec 002 task T-am-2 (§7), Verify line `pytest -k test_split_guard`.

## 6. Design deltas

| Where | Old | New |
|---|---|---|
| spec 002 design:50 | "Fresh hold-out is scored after training and reported separately" | "Validate is scored after training (selection context); test is scored once, after the operating point is frozen (evidence)" |
| spec 002 design:53 | sweep "against the hold-out" | sweep "against **validate** only" |
| spec 002 design:9 | "against the fresh hold-out" | "against the fresh **test** set" |
| spec 002 design:122/135 | single "honest fresh-hold-out reporting" row | validate-selection vs test-evidence reported separately; smoke run names both |
| spec 001 design:111 | "hold-out validator rejects `synthetic`" | "validate/test validators reject `synthetic` and `aug_*`" |
| report keys | `holdout_fresh`, val-split | `test`, `validate`, `train_val` |

Artifacts:

- `build/holdout_report.md` → `build/validate_report.md` + `build/test_report.md`
- `build/sweep_report.json` unchanged (it is a validate artifact by definition now)

## 7. Task deltas

Spec 001 (`.ai/sdd/specs/001-can-hear/tasks.md:88,96`): rename `holdout/` → `validate/` + `test/`
in T5/T7 text and Verify lines; the synthetic-isolation check covers both reserved sets.

Spec 002 (`.ai/sdd/specs/002-can-think/tasks.md:77,82`) — new tasks, to be appended after approval:

| Task | Covers | Verify |
|---|---|---|
| T-am-1 — three-way split in `features.py` / `train.py` (both sides), reports renamed, sweep reads validate only | NFR-003, NFR-005, FR-032 | `uv run ultron features && uv run ultron train run` on a fixture with all three splits; `train sweep` output names validate as its input |
| T-am-2 — selection guard | FR-037 | `pytest -k test_split_guard` (sweep/dir pointing at `test/` exits non-zero, message names the split) |
| T-am-3 — rename sweep of §9 (flags, constants, docs, tests), no behaviour change | NFR-006 | `pytest -q` green; `grep -ri "holdout\|hold-out" cli model tests docs README.md` returns only the deprecation note (if any) |

Coverage table (002 tasks) gains the FR-037 row; skipping it would be a spec gap.

## 8. Migration of the current dataset

1. `dataset/holdout/` (8 clips, 13 windows — already consumed by a sweep on 2026-11-18) becomes
   `dataset/validate/`: that is what it actually was.
2. `dataset/test/` must be **fresh recordings**; none exist. Until they do, no NFR-003 claim may
   be made from this dataset.
3. With 41 clips the three-way minimums (16 reserved per variant) are out of reach — the current
   `--min-originals 0` deviation stays recorded in `build/train_report.json` and must not be
   quoted as meeting NFR-005.
4. Renaming on disk is owner-visible but non-destructive (files move inside the gitignored
   `dataset/`); no commit, no data loss.

## 9. Rename footprint (T-am-3)

~180 occurrences, counted 2026-11-18:

| Area | Files | count |
|---|---|---|
| training/CLI source | `model/src/ultron_model/train.py` (38), `cli/src/ultron/train.py` (37), `cli/src/ultron/features.py` (20), `dataset.py` (5), `synth.py`, `cli.py` | 102 |
| tests | `test_train_smoke.py` (35), `test_synth.py` (8), `test_infer.py` (7), `test_features.py` (6), `test_export_smoke.py` (3) + 5 more files | 10 files |
| specs | 001 design/tasks/requirements, 002 design/requirements/tasks | 6 files |
| docs & steering | `README.md`, `docs/recording.md`, `docs/dataset.md`, `.ai/steering/conventions.md`, `.ai/steering/principles.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `.agents/skills/conventional-commits/SKILL.md` | 7 |

Identifiers renamed: `--holdout` → `--dataset-root`; `--out-holdout-md` → `--out-validate-md` +
`--out-test-md`; `extract_holdout()` → `extract_split(root, labels, split)`; `run_sweep(ckpt,
validate)`; `write_holdout_report_md()` → `write_split_reports_md()`; `holdout/` → `validate/` /
`test/` everywhere else.

## 10. Open questions — owner decisions, not assumptions

- **Q-A1: reserved-set size.** Drafted as 8 validate + 8 test per variant (NFR-005). With 41 clips
  that is unattainable; the cheaper alternative is 4 validate + 8 test (keeps more training data,
  weakens threshold selection). *(Recommendation: keep 8 + 8 in the spec and treat the current
  dataset as explicitly non-conforming — lowering a minimum to match a dataset we happen to have
  is how the definitions rot.)*
- **Q-A2: freshness of validate vs test.** Should the test set be recorded in a different
  session/day (and ideally a different room) than validate, so operating-point tuning cannot
  absorb session character? *(Recommendation: yes — same-session test clips share mic, gain and
  room tone, which is exactly what the sweep would exploit.)*
- **Q-A3: flag compatibility.** Hard rename `--holdout` → `--dataset-root` now, or accept
  `--holdout` as a deprecated alias for one release? *(Recommendation: hard rename — the repo is
  pre-release and an alias keeps the old noun alive in muscle memory.)*

## 11. Risks

| Risk | Mitigation |
|---|---|
| Recording work roughly doubles (two reserved sets instead of one) | Reserve progressively: validate now, test after the next recording session; no NFR-003 claim until both exist |
| Renaming touches already-approved spec text | This file is the only change until approval; spec bodies edited in the amendment PR after the gate flip, in one commit |
| Test set read by hand during debugging ("just to check") | FR-037 guard for code; PR template line for humans |
| Small datasets get worse before better (33 → ~25 training clips if all three splits are carved from 41) | Do not carve three ways from this dataset — record more first (§8.3) |

## 12. Approval

- [ ] Owner approves the requirement deltas in §5 (NFR-003 reword, NFR-005 minimums, FR-037)
- [ ] `.status` flips: 001 + 002 `requirements:amended → approved`; then design delta (§6), then
      tasks (§7), then implementation (T-am-1…3)
- [ ] Q-A1…Q-A3 answered (each answer edits §5/§9 before approval, not after)
