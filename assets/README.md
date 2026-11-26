# `assets/` — fixed audio feedback clips

Pre-rendered confirmation clips for spec 003-can-act (FR-018, FR-037, D-006, Q-012).
They are **rendered once, committed as WAV bytes and played byte-identically** by every
runtime. No device performs runtime speech synthesis; the Orange Pi (spec 005) will play
these exact files through its USB audio dongle.

| File | Spoken | Voice | Rate | Format | Duration | Budget | Samples | sha256 (WAV) |
|---|---|---|---|---|---|---|---|---|
| `escuchando.wav` | "Escuchando" | Mónica (es_ES) | 300 wpm | 16 kHz / 16-bit / mono | **441 ms** | ≤ 500 ms (NFR-010) | 7 058 | `82d4885bbdf3dfcfa7fbcea4fbab70258617a8009966e3c1b539c378ff22960c` |
| `comando.wav` | "Comando" | Mónica (es_ES) | 340 wpm | 16 kHz / 16-bit / mono | **256 ms** | ≤ 300 ms (NFR-010) | 4 094 | `0be8015b30d62e1ab7ab972b94c3331920bf86d7bbd756b965b755399b015da6` |

Both clips are peak-normalised to **−1 dBFS** and carry a 4 ms fade in/out (no edge
clicks). Silence is trimmed at both ends below −45 dBFS.

## Re-rendering

```sh
./scripts/render_feedback_assets.sh    # macOS: say + ffmpeg + python3
uv run python scripts/gen_feedback_clips.py   # WAVs → devices/esp32/ultron_voice/clips.h
```

The script fails if a clip lands outside its budget, so the `say` rate is the knob:

| `say -r` for "Escuchando" | 180 | 220 | 260 | 300 |
|---|---|---|---|---|
| measured duration | 688 ms | 565 ms | 508 ms | **441 ms** |

Rate 300 is the slowest rate that fits 500 ms with the tail trim; "Comando" (three
syllables) needs rate 340 to fit 300 ms. `say -v '?'` lists the other Spanish voices
(Paulina es_MX, Eddy/Flo/Reed/Rocky es_ES …) — changing voice or rate changes the bytes,
so re-run the generator and commit both the WAVs and `clips.h` together.

The firmware does not read these files at build time: `clips.h` embeds the PCM so an
`arduino-cli compile` needs nothing but the checked-in header. `tests/test_feedback_clips.py`
re-renders the header from these WAVs and fails on drift.
