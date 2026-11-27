# Recording — ultron WAV recorders (T4)

The XIAO records **16 kHz / 16-bit / mono WAV** clips labeled by phrase, producing files that
`ultron dataset validate` accepts (FR-021 / US-002 / NFR-005). Output naming follows the repo
convention `{label}_{mic}_{nn}.wav` with `mic ∈ {xiao, ext}` — one target (owner decision
2026-11-26), so there is no Pi tag any more; legacy `*_pi_*.wav` files are renamed with
`uv run ultron dataset retag-pi <dir> --apply`.

## Devices

| Device | Recorder | Mic | Storage |
|--------|----------|-----|---------|
| Seeed XIAO ESP32S3 Sense | `devices/esp32/wav_recorder/wav_recorder.ino` | built-in PDM | SD card |
| Dev host (local) | `devices/local/record.py` | default input mic | filesystem |

## XIAO ESP32S3 Sense — `wav_recorder.ino`

Serial-driven. Send `rec {label}` over the serial console (115200 baud):

```
rec guiñador_derecho
```

Behavior:
- Replies `OK /sdcard/<label>_xiao_<nn>.wav` when recording starts.
- Records 5 s of 16 kHz/16-bit/mono PDM audio to the SD card.
- Replies `DONE <bytes>` when the WAV is closed and the header is fixed up.
- Replies `ERR <msg>` on SD/mic/buffer failure.

Label slugs are lowercase ASCII (`guiñador_derecho`, `luz_media`, …); the
registry ids from `model/registry.json` are the canonical label set.

## Dev host (local) — `record.py`

Same interface as the XIAO recorder, but uses the host's default input device:

```bash
python3 devices/local/record.py <label> [--seconds 5] [--device DEVICE] [--out-dir DIR]
```

Writes `<label>_local_<nn>.wav`. Falls back to the first detected capture
device when no default input is set; fails fast listing devices when none is
present.

Example:
```bash
python3 devices/local/record.py guiñador_derecho --seconds 5 --out-dir dataset/originals/guiñador_derecho
```

```

## Host-side checks (this PR)

- Python syntax + import check for `record.py`.
- Argument parsing exercised (`--help`, missing label, bad device).
- WAV header writing unit-tested with a fake `sounddevice` stream: the bytes
  `record.py` produces pass a 16 kHz / 16-bit / mono validation check (the same
  checks `ultron dataset validate` performs — FR-021).

## On-device verification (T7 — issue #10, deferred)

The following require the physical devices and are **not** run here:

- XIAO records a 5 s WAV to SD card and emits the `DONE` response (AC1).
- A capture from an owner-supplied mic (tagged `ext`) passes `ultron dataset validate`
  (AC2 / AC3). The Pi half of US-002 was removed with the Pi target.

These are consolidated in issue #10 (T7 — hardware validation). `docs/recording.md`
is updated there with the measured results once T7 runs.

## Labels & mic tags

Files produced by these recorders are ready for `ultron dataset ingest`:
- XIAO: `dataset/originals/<label>/<label>_xiao_<nn>.wav`
- local: `dataset/originals/<label>/<label>_local_<nn>.wav`
- ext:  owner-supplied (phone/PC) recordings, tagged `ext`
- legacy `*_pi_*.wav` → `uv run ultron dataset retag-pi <dir> --apply` (the `pi` tag is gone)

NFR-005: ≥ 20 originals per accepted phrase variant, recorded from all mics (xiao, ext, local).

---

## Synthetic data (T5.1) — `ultron synth tts` / `ultron synth noise`

Machine-generated training audio, kept strictly supplementary per the
lampara-aliz lesson:

- Tagged `synthetic` by layout (`dataset/synthetic/<label>/`) and by filename
  prefix (`syn_*`).
- **NEVER enters the fresh hold-out** — `ultron dataset validate` fails if a
  `syn_*` clip appears under `dataset/holdout/` (NFR-005 hygiene).
- **Never counts toward NFR-005 original minimums** — synthetic is tracked
  separately from originals.
- **Training use is opt-in only** — synthetic clips enter training via
  `--with-synthetic` (default **off**; the owner decides per run). Every use is
  a changelog event.

### `ultron synth tts`

Renders **every registered phrase** (`model/registry.json`: wake + both forms of
each of the 10 commands) in several Spanish TTS voices x speaking speeds,
seeded — same seed => identical synthetic set:

```
ultron synth tts                                       # seed 42, mock engine offline (or `say` on macOS)
ultron synth tts --seed 7 --engine say --voices Mónica,Paulina --speeds 0.9,1.0,1.1
ultron synth tts --dry-run                             # lists every target, writes nothing
ultron synth tts --output dataset/synthetic
```

Outputs are 16 kHz / 16-bit / mono WAV under `dataset/synthetic/<label>/`.

### `ultron synth noise`

Assembles room ambience and car/engine sounds for background mixing:

```
ultron synth noise                                     # 8 clips per source (room, car), 4 s each, seed 42
ultron synth noise --sources room,car --count 10 --seconds 5 --seed 42
ultron synth noise --dry-run                           # lists the assembly plan, writes nothing
```

Outputs under `dataset/synthetic/noise/<source>/`, 16 kHz / 16-bit / mono WAV.

### Offline / no network TTS

There is **no network TTS dependency** by default: on macOS the `say` engine
(Spanish voices) is used offline; everywhere else, or with `--engine mock`, a
deterministic seed-driven stand-in renders the clips. Tests run against the
mock engine and skip `say`/ffmpeg-dependent cases when those tools are absent.

### `ultron dataset validate`

```
ultron dataset validate dataset                        # default path: dataset/
```

Checks every clip is 16 kHz / 16-bit / mono and rejects synthetic-tagged clips
in the fresh hold-out (exit non-zero, listing each violation).
