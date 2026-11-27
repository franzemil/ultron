# AGENTS.md — guidance for AI agents working on this repo

Ultron: Spanish voice-actuation **car manager** — always-on **"hey ultron"** wake word, then a
command phrase (e.g. *"enciende guiñador derecho"*) within a 5 s listening window, driving
9 relay channels (LM LB LA GD GI LPB LPA A CC). One PyTorch → TFLite-Micro pipeline on a
**single target — the Seeed XIAO ESP32S3 Sense** (owner decision 2026-11-26: the Orange Pi Zero 2W
is out of scope and its code is removed). **Source of truth is this repo — Edge Impulse is
export-only.**

## Development flow (Spec-Driven Development)

1. Every feature has a spec in `.ai/sdd/specs/NNN-name/`
2. Read the feature's `.status` file BEFORE implementing anything
3. Never write implementation code before `tasks:approved`
4. Phase order: requirements → design → tasks, each gated by explicit human approval
5. Ambiguity during implementation: STOP and ask
6. `.ai/sdd/INDEX.md` is a dashboard, not truth — the `.status` file is truth
7. File existence does not imply approval; only `*:approved` in `.status` unlocks the next phase

## Mandatory rules

1. **Changelog discipline**: every inference-affecting change (dataset, augmentation, DSP, model
   architecture, training, thresholds, conversion, device (esp32/Pi) inference path) is
   documented in its **PR title/description** with a conventional prefix (`feat:`, `fix:`,
   `dataset:` …) — `CHANGELOG.md` is generated from PR merges and is never hand-edited.
2. Never commit secrets or data: `.env`, `dataset/`, `build/`, `.venv/` stay gitignored.
3. **Edge Impulse is one-way**: `ultron ei sync` exports data/model artifacts to EI. Never import
   EI artifacts over local ones without asking the user first.
4. Destructive dataset/EI operations require a printed target list and explicit user confirmation.
5. **Single target (owner decision, 2026-11-26): the XIAO ESP32S3 Sense.** The Orange Pi Zero 2W is
   out of scope: there is no second runtime to keep in step, and the removed Pi code is not to be
   reintroduced without an owner decision. Should a second target ever return, it must consume the
   **identical model flatbuffer** and identical event semantics — divergence would be a bug.
