# Recording — ultron WAV recorders (T4)

Both candidate devices record **16 kHz / 16-bit / mono WAV** clips labeled by
phrase, producing files that `ultron dataset validate` accepts
(FR-021 / US-002 / NFR-005). Output naming follows the repo convention
`{label}_{mic}_{nn}.wav` with `mic ∈ {xiao, pi, ext}`.

## Devices

| Device | Recorder | Mic | Storage |
|--------|----------|-----|---------|
| Seeed XIAO ESP32S3 Sense | `devices/esp32/wav_recorder/wav_recorder.ino` | built-in PDM | SD card |
| Orange Pi Zero 2W | `devices/orangepi/record.py` | owner-supplied USB | filesystem |

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

## Orange Pi Zero 2W — `record.py`

```bash
python3 devices/orangepi/record.py <label> [--seconds 5] [--device DEVICE] [--out-dir DIR]
```

- `<label>`: phrase label slug (positional, required).
- `--seconds`: capture duration (default 5).
- `--device`: sounddevice input device index or name substring (owner USB mic).
- `--out-dir`: output directory (default `.`).

Writes `<label>_pi_<nn>.wav`. Fails fast (exit 2) listing detected capture
devices when no microphone is present — no silent fallback to device 0
(Q-003).

Example:
```bash
python3 devices/orangepi/record.py guiñador_derecho --seconds 5 --out-dir dataset/originals/guiñador_derecho
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
- Pi captures a real WAV from the owner USB microphone and `ultron dataset
  validate` passes it (AC2 / AC3).

These are consolidated in issue #10 (T7 — hardware validation). `docs/recording.md`
is updated there with the measured results once T7 runs.

## Labels & mic tags

Files produced by these recorders are ready for `ultron dataset ingest`:
- XIAO: `dataset/originals/<label>/<label>_xiao_<nn>.wav`
- Pi:   `dataset/originals/<label>/<label>_pi_<nn>.wav`
- ext:  owner-supplied (phone/PC) recordings, tagged `ext`

NFR-005: ≥ 20 originals per accepted phrase variant, recorded from both mics.
