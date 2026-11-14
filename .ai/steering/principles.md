# Ultron — Principles

Non-negotiable rules. If a change violates one of these, stop and discuss.

1. **The repo is the source of truth.** Edge Impulse is an export/archive target. Model bytes
   never flow from EI into this repo.
2. **One MFCC implementation.** Training features, XIAO inference, and Pi inference all use the
   same C code in `dsp/`. Any second feature implementation is a bug.
3. **No model ships without a parity report** (PyTorch vs exported TFLite argmax agreement
   ≥ 99% on the validation split) **and** a fresh-hold-out evaluation.
4. **Both targets are identical.** Same flatbuffer, same thresholds, same event semantics.
   Divergence between XIAO and Pi is a bug, not a platform quirk.
5. **Every inference-affecting change is documented in its PR** (the changelog is generated from
   PR merges). No exceptions, no "small" tweaks.
6. **Audio math on device is integer/float-32 lightweight; no dynamic allocation in the
   inference loop.** Buffers are static; the TFLM arena is sized once.
7. **Reproducibility is mandatory.** If a run can't be reproduced from seed + recorded versions,
   it doesn't count as a result.
8. **If you are uncertain whether something is in scope, it is not.** Ask.
