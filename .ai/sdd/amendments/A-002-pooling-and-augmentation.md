# A-002 — Pooling + regularisation, and two real augmentation variants

> Amendment to [spec 001](../specs/001-can-hear/) (T5 augmentation) and
> [spec 002](../specs/002-can-think/) (design §Model, T8 training).
> Owner directive 2026-11-19: "let's apply the 1 and 2" (the two fixes ranked in the
> la/lb feature analysis of the same day).

| Field | Value |
|---|---|
| Kind | design amendment (architecture + augmentation), no new FR/NFR IDs |
| Raised | 2026-11-19 |
| Gate | `amendment:A-002:applied` — implemented in the same change, measurements below |
| Affects | `NFR-003` (evidence improves), `NFR-005` (augmentation count 4 → 5), Q-008 op set (kept) |

## 1. Why — the la/lb measurement

`la` ("luz alta") and `lb` ("luz baja") sat in the same region of feature space: on the
project archive (1,638 windows then), `la`↔`lb` centroid distance was **8.6** against
26–31 for every other pair, and the model failed a hold-out `la` window with an exact
`0.5000 / 0.5000` tie. Clip-grouped 5-fold CV (no augmentation leaked across folds) said
the information *is* present, and that pooling is what discards it:

| features | dims | la vs lb CV accuracy |
|---|---|---|
| `mean` only — what `GlobalAvgPool` gave the CNN | 13 | 87.8% ± 8.1 |
| `mean + std` | 26 | 90.2% ± 5.7 |
| `mean + mean(|Δ|)` | 26 | 89.8% ± 6.4 |
| `mean + std + mean(|Δ|)` | 39 | 90.8% ± 5.3 |
| all 1,300 dims flattened | 1300 | 86.1% ± 9.4 |
| majority-class baseline | — | 59.9% |

So: keep the 13 MFCCs (the DSP contract stays untouched), keep temporal spread, and stop
padding every utterance at offset 0.

## 2. Design change — `KwsNet`

Old (design §Model, frozen):

```
Conv1d(13→16, k3, relu) → MaxPool1d(2) → Conv1d(16→32, k3, relu) → GlobalAvgPool1d
→ Linear(32→16, relu) → Linear(16→C, softmax)
```

New:

```
Conv1d(13→16, k3, relu) → MaxPool1d(2) → Conv1d(16→32, k3, relu)
→ mean+MAD pool over time (64) → Dropout(0.2) → Linear(64→16, relu) → Linear(16→C, softmax)
```

- **Tensor pooling**: `mean` plus mean absolute deviation of the centred activations
  (`pool_stats()`), not `E[x²] − E[x]²` and not `sqrt`.
- **Dropout 0.2** on the pooled vector.
- **Weight decay 1e-4** in Adam.
- Params 2.9k → 3.3k; int8 artifact 10.5 KB (budget ≤ 100 KB).

### Two int8 findings that shaped the final form

1. `E[x²] − E[x]²` **cancels catastrophically** once activations are 8-bit
   (`E[x²] ≈ 1300` vs `E[x]² ≈ 64`): the parity gate failed at 98.70% with exact
   `0.5/0.5` output ties while fp16 was confident (0.994). Fixed by centring first.
2. `sqrt` then amplified error near zero (infinite derivative), still 98.70%. Replacing
   the spread with **mean absolute deviation** (no `sqrt`) restored the gate:
   **int8 99.80%**, fp16 100.00% (FR-011 / NFR-007, ≥ 99%).

The op set stays inside Q-008 (`mean`, `sub`, `abs`, `mul`, `concat`); no new onnx2tf-risky
op, and the `Squeeze` pattern that onnx2tf mis-transposes is avoided entirely
(`unsqueeze` broadcast instead).

## 3. Augmentation change — T5

`VARIANTS` 4 → 5:

| variant | before | now |
|---|---|---|
| `gain`, `tempo`, `pitch` | unchanged | unchanged |
| `mix` | **no-op in practice**: `aecho` 50% of the time, `volume=1.0` otherwise — no noise was ever mixed | loops a real noise clip from `dataset/originals/noise` (then `synthetic/noise`) under the speech at 0.15–0.50 gain; falls back to a flat copy only when the dataset has no noise at all |
| `shift` | — | new: moves the utterance to a random offset of the 2.0 s window (`adelay` + `apad` + `atrim`); clips longer than 2 s are trimmed to a random 2.0 s excerpt |

Rationale: every training window previously put the phrase at sample 0 with silence after
it, while a live sliding window catches it anywhere. `shift` is what teaches offset
invariance without touching the DSP or the device contract.

NFR-005 says "≥ 4 seeded augmentations per original"; 5 is ≥ 4, and the plan stays
seed-deterministic.

## 4. Measured result (project archive, seed 42)

| | before | after |
|---|---|---|
| windows | 1,638 | 3,941 |
| hold-out accuracy | 84.62% (la 50%, lb 85.7%, noise 50%, wake 100%) | 76.92% (la **100%**, lb 85.7%, noise **0%**, wake 100%) |
| int8 parity | 99.30% | **99.80%** |

**Honest reading**: `la` recall went 50% → 100% and the parity gate stayed green, but the
hold-out is **13 windows** (2 la, 2 noise), so 1 window = 7.7% and the overall 84.62% →
76.92% drop is dominated by `noise` (0/2 — the two smallest clips in the set). The dataset
also changed between the two rows (`la` 46 → 106 originals), so this is not a controlled
A/B. **No claim of improvement is made until the hold-out reaches ≥ 8 clips per label**
(A-001).

## 5. Follow-ups

- [ ] Grow `dataset/holdout/` to ≥ 8 clips/label, then re-run this A/B properly.
- [ ] Re-sweep thresholds against the new checkpoint (`train sweep`, then copy to `build/`).
- [ ] `noise` recall deserves its own investigation: 2 hold-out windows, both very short.
- [ ] If a further architecture change is desired, consider `mean+std+mean|Δ|` (90.8% CV)
      together with quantization-aware training, since plain `sqrt` std failed the gate.
