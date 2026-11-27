# Ultron — Conventions

## Repository layout

```
AGENTS.md                 # agent router — read first
CHANGELOG.md              # generated from PR merges — never hand-edited
.agents/skills/           # portable agent skills — conventional-commits (OpenCode + pi.dev)
.github/                  # changelog workflow + PR template
pyproject.toml            # uv workspace root (members: cli/, model/)
.ai/                      # SDD governance (steering + specs)
dsp/                      # "ears": shared C MFCC — the ONE feature implementation
model/                    # "brain": PyTorch package — model.py, train.py, export.py
                          #   + registry.json, thresholds.json (versioned configs)
cli/                      # tooling: uv package — src/ultron/ Typer CLI
                          #   (dataset, augment, features, esp32, export, ei sync)
devices/
  esp32/                  # XIAO ESP32S3: ultron_voice/ sketch + libraries/UltronVoice/
tests/                    # pytest, mirrors all components
docs/                     # topic docs: model.md, retraining.md, bench.md, troubleshooting.md
dataset/                  # gitignored — never commit audio
build/                    # gitignored — generated artifacts (features.npz, .onnx, .tflite, reports)
```

Python packaging is a **uv workspace**: the root `pyproject.toml` declares members `cli/` and
`model/`; the CLI imports the model code as a workspace dependency.

## Naming

- Python files and modules: `snake_case`. Arduino sketches: directory name = `.ino` name.
- Labels: lowercase ASCII sigla slugs — `wake`, `noise`, `lm`, `lb`, `la`, `gd`, `gi`, `p`, `cp`,
  `lpb`, `lpa`, `a`, `cc`. Registry IDs are stable across renames of the human phrases.
- WAVs: originals `{label}_{mic}_{nn}.wav` with mic ∈ `xiao|pi|ext` (`ext` = owner-supplied
  external recordings, e.g. phone/PC); augmentations `aug_{label}_{mic}_{nn}_{k}.wav`; synthetic
  `syn_{label}_{voice}_{nn}.wav`.
- CLI verbs follow lampara-aliz style: `ultron <group> <action>` (e.g. `dataset dcfix`,
  `train run`, `export tflite`, `ei sync`).

## Data hygiene

- Audio is always **16 kHz / 16-bit / mono** WAV, 1–6 s.
- All utterances are **Spanish** (registry `language: "es"`) — prompts are spoken naturally as
  the owner would say them in the car, not read robotically.
- `dataset dcfix` (DC-offset removal) runs before feature extraction; NaN features fail loudly
  instead of silently training on garbage (lampara lesson: NaN → DSP error −5).
- Splitting is **stratified and seeded**: `rng(42)` everywhere. Same seed ⇒ identical splits,
  augmentations, and metrics.
- A **fresh hold-out** set (recorded after training data, never augmented into training) is the
  only accuracy number we report publicly. Val-split accuracy is memorization + augmentation
  coverage and is labeled as such.
- **Synthetic data policy**: TTS/noise-generated clips are opt-in for training
  (`--with-synthetic`, default off), never enter hold-out, never count toward NFR-005 original
  minimums; every use is a changelog event.

## Reproducibility

- Every training run is recorded in its **PR description**: git rev, dataset counts per label,
  seed, and the pinned versions (torch, onnx, onnx2tf, tensorflow, python) — the PR-generated
  changelog carries it into releases.
- Thresholds live in `model/thresholds.json` (versioned); tuning a threshold is a
  PR-documented event.

## Docs

- One topic per file in `docs/`, same style as lampara-aliz: hardware, model, retraining, bench,
  troubleshooting. README stays a router, not an encyclopedia.
- `docs/bench.md` holds the XIAO-vs-Pi comparison protocol and the results table.
- **Diagrams are Mermaid** (```` ```mermaid ```` fenced blocks) — never ASCII art.
- **Changelog & commits**: `.github/workflows/changelog.yml` auto-appends one bullet per merged
  PR to `CHANGELOG.md` (bot commit, `[skip changelog]`). Commit messages and PR titles follow the
  `@conventional-commits` skill (`.agents/skills/` — works in OpenCode **and** pi.dev); PR
  descriptions of inference-affecting changes carry the NFR-006 metadata.

## Spec conventions

- User stories use the standard format, each followed by an acceptance-criteria block with
  independently testable ACs:

  ```markdown
  ### US-001 — Title
  **As a** <role>
  **I want to** <capability>
  **so that** <benefit>.

  **Acceptance criteria:**
  - **AC1:** <concrete, testable outcome>.
  ```

- Functional requirements use **EARS** syntax (`WHEN/WHILE/IF/WHERE … THE SYSTEM SHALL …`) with
  MoSCoW priority on every requirement.
- Output behaviors enum (spec 003): `steady`, `blink_45s`, `blink_until_cancel`, `cancel`,
  `motor_45s`, `pulse_2s`, `pulse_05s`.
- Stable IDs everywhere: US-001, FR-001, NFR-001, D-001, TD-001, Q-001, T1.
- Task sub-IDs use **decimal** suffixes (`T5.1` is the only current one) — never letters.
