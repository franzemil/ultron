# ultron

Voice-actuation **car manager**: says **"hey ultron"** to wake it, then a Spanish command
(*"enciende guiñador derecho"*, *"arranque"*, …) drives 9 relay channels. Runs the **same
PyTorch → TFLite pipeline** on two
candidate devices — **XIAO ESP32S3 Sense** and **Orange Pi Zero 2W** — and a benchmark picks the
winner. Successor to [`lampara-aliz`](../lampara-aliz) (Edge Impulse-based); this project trains
locally with **PyTorch as source of truth**, Edge Impulse is export-only.

## Status

🚧 **Spec phase — no implementation code yet.**

## Status

Implementation in progress (Milestone 1 — "can hear"): CLI, dataset tooling,
recorders (T2–T4), seeded augmentation (T5), and synthetic data generation
(T5.1). Specs & gates: `.ai/sdd/specs/001-can-hear/`.

## CLI quick reference

```
uv run ultron --help
uv run ultron dataset validate dataset          # 16 kHz/16-bit/mono + hold-out hygiene
uv run ultron synth tts --dry-run               # list TTS targets, write nothing
uv run ultron synth tts                         # render every registered phrase x voice x speed
uv run ultron synth noise                       # assemble room/car noise corpus
```

## Synthetic data (augmented) — `ultron synth tts` / `ultron synth noise`

T5.1 adds machine-generated training audio, kept strictly supplementary per the
lampara-aliz lesson:

- Tagged `synthetic` (`dataset/synthetic/<label>/`, `syn_*` names).
- **Never enters the fresh hold-out** — `dataset validate` rejects `syn_*`
  clips under `holdout/` (NFR-005 hygiene).
- **Never counts toward NFR-005 original minimums** — tracked separately.
- **Training use is opt-in only** — via `--with-synthetic` (default off);
  every use is a changelog event.

### `ultron synth tts`

Renders every registered phrase (wake + both forms of each of the 10 Spanish
commands) in several TTS voices x speaking speeds, seeded — same seed =>
identical synthetic set:

```
uv run ultron synth tts                              # seed 42, mock offline (or `say` on macOS)
uv run ultron synth tts --seed 7 --engine say --voices Mónica,Paulina \
                                                      --speeds 0.9,1.0,1.1
uv run ultron synth tts --dry-run                    # lists targets, writes nothing
```

Outputs: 16 kHz / 16-bit / mono WAV under `dataset/synthetic/<label>/`.

### `ultron synth noise`

Assembles room ambience and car/engine sounds for background mixing:

```
uv run ultron synth noise                            # 8 clips per source (room, car), 4 s each
uv run ultron synth noise --sources room,car --count 10 --seconds 5 --seed 42
uv run ultron synth noise --dry-run                  # lists the plan, writes nothing
```

Outputs: 16 kHz / 16-bit / mono WAV under `dataset/synthetic/noise/<source>/`.

Method: [Spec-Driven Development](https://github.com/felipefontoura/spec-driven-development-book/blob/main/BOOK.en.md).

| Artifact | Where |
|---|---|
| Product plan | [.ai/sdd/PLAN.md](.ai/sdd/PLAN.md) |
| Milestone specs 001–005 | [.ai/sdd/specs/](.ai/sdd/specs/) — dashboard: [INDEX.md](.ai/sdd/INDEX.md) |
| Gate status | per-spec `.status` files (see INDEX) |
