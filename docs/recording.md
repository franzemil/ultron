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
- Then prints `DBG peak=<n> mean=<n> samples=<n>`: the take's peak level and ADC
  mean. `mean` is the DC offset US-002 AC3 talks about — non-zero means run
  `ultron dataset dcfix` before training. `peak=0` means the mic saw nothing.

Label slugs are lowercase ASCII (`guiñador_derecho`, `luz_media`, …); the
registry ids from `model/registry.json` are the canonical label set. The sketch
rejects anything outside `[a-z0-9_]` with `ERR invalid_label`.

### Capture path — PDM RX through `ESP_I2S` (T28, supersedes `PDM.h`)

The sketch used the old Arduino `PDM` driver until T28. **That driver no longer
exists**: ESP32 Arduino core 3.x dropped `PDM.h` (it shipped in 2.0.x only), so
the sketch did not compile on the project toolchain:

```
$ find ~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.12 -name PDM.h
$                                 # no output — PDM.h is gone in core 3.3.12
$ arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/wav_recorder
wav_recorder.ino:23:10: fatal error: PDM.h: No such file or directory
```

The port keeps the **same peripheral** and the **same pins**, with no toolchain
downgrade: the ESP32-S3 supports PDM RX in hardware
(`SOC_I2S_SUPPORTS_PDM_RX = 1`) and core 3.3.12 ships `ESP_I2S` with
`I2S_MODE_PDM_RX`, `initPDMrx()` and `setPinsPdmRx()`:

```cpp
#include "ESP_I2S.h"
static I2SClass i2s;
i2s.setPinsPdmRx(/*clk=*/40, /*din0=*/41);
if (!i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
  /* ERR mic_begin_failed */
}
const size_t got = i2s.readBytes((char *)buf, bytes);  // int16 LE, mono, 16 kHz
```

`ESP_I2S` returns already-decimated 16-bit PCM, which is exactly the dataset
contract — no conversion step. Because `readBytes()` blocks for at most the
stream timeout, the recording loop is a simple chunked copy to the SD file
(512 samples = 32 ms per write) with a `ERR short_record <bytes>` reply if the
mic stalls mid-take.

> **For T12 (firmware core):** this is the capture path the firmware needs too —
same two pins, same rate, same 16-bit mono output. The T28 pass deliberately did
**not** create a shared `devices/esp32/common/pdm_capture.{h,cpp}` module (none
existed on `origin/main` and T12 was writing one in parallel); adopt the
sequence above rather than the removed `PDM.h` API. The ring-buffer / 500 ms hop
logic stays in the firmware, not in the recorder.

### Retrieving the WAVs — `devices/esp32/tools/xiao_pull.py`

The microSD card sits inside the Sense expansion, so the recorder also serves
its files over the same USB port. Two extra commands, and a stdlib-only host
tool that drives them:

| Command | Reply |
|---|---|
| `ls` | `LIST <name> <bytes>` per file, then `LISTDONE <count>` |
| `get <name>` | `BEGIN <name> <bytes>`, `<raw bytes>`, `END` |
| `mic` | `MIC peak=<n> mean=<n> samples=<n>` — 200 ms PDM read, no SD needed |
| `help` | the protocol line (also the liveness probe) |

```bash
# what is on the card?
python3 devices/esp32/tools/xiao_pull.py ls

# is the mic alive? (bring-up: separates a dead mic from a missing card)
python3 devices/esp32/tools/xiao_pull.py mic

# one 5 s take per trained label, pulled to /tmp/xiao_takes/<label>/
python3 devices/esp32/tools/xiao_pull.py session --labels wake,noise,la,lb --takes 3 \
    --out /tmp/xiao_takes

# pull everything already recorded
python3 devices/esp32/tools/xiao_pull.py pull --out /tmp/xiao_takes

# one take only
python3 devices/esp32/tools/xiao_pull.py rec la --out /tmp/xiao_takes/la
```

The tool needs no pyserial: it opens the tty raw, asserts DTR/RTS (which is what
makes the S3 `HWCDC` console transmit) and reads the dump framing itself. It
re-checks every pulled clip with the stdlib `wave` module and refuses anything
that is not 16 kHz / 16-bit / mono.

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

## Dev host (local) — `record.py`

Same interface as the Pi recorder, but uses the host's default input device:

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

**Update (T28, 2026-11-21):** the XIAO half of this check is the device-mic
recording pass below. The recorder now compiles on the project toolchain (the
`PDM.h` blocker is fixed) and the retrieval/ingest/parity pipeline is in place;
the on-device recording itself is **still pending** — see the session log.

## T28 — Device-mic recording pass (US-002, XIAO clause)

**Session date:** 2026-11-21 · **Host:** macOS 26.6 (arm64) · **Spec:**
[003-can-act T28](../.ai/sdd/specs/003-can-act/tasks.md) · **Status:** tooling
landed and compile-verified; **the on-device recording is blocked on hardware
(see [Blocked on hardware](#blocked-on-hardware-device-hung-before-the-first-take)) — no
recording, validation or parity number below is invented.**

### Hardware

| Item | Value |
|---|---|
| Board | Seeed XIAO ESP32S3 **Sense** (expanded version with the PDM mic + microSD slot) |
| USB | Espressif native USB, `VID 0x303A PID 0x1001` → `/dev/cu.usbmodem2101` |
| Mic | on-board PDM (MSM261D3526H1CPM): data `GPIO41`, clock `GPIO40` — same pins the pre-T28 sketch used; confirm with `xiao_pull.py mic` on the first live session |
| Storage | microSD in the Sense slot, SPI, CS `GPIO10` (never reached on hardware yet — see the blocker section) |
| Toolchain | `arduino-cli` 1.5.1, ESP32 core **3.3.12**, `esp32:esp32:XIAO_ESP32S3`, USB mode *Hardware CDC and JTAG*, CDC on boot enabled |

### What the pass does

1. Flash the recorder, record ≥ 1 take per **trained** label (`wake`, `noise`,
   `la`, `lb` — spec 002 TD-012), pull the WAVs over USB.
2. Ingest them with the `xiao` mic tag so a later retrain can tell device audio
   from the host-mic (`local`) originals.
3. Validate the dataset and run MFCC parity on the **device** features, not just
   the synthetic fixtures.

### Session log — clip counts

| Label | Takes this session | Required for the next retrain (NFR-005) |
|---|---|---|
| `wake` | **pending (hardware blocked)** | ≥ 30 originals |
| `noise` | **pending (hardware blocked)** | ≥ 30 originals |
| `la` | **pending (hardware blocked)** | ≥ 40 originals |
| `lb` | **pending (hardware blocked)** | ≥ 40 originals |

### Commands the owner runs (exact, in order)

Everything below holds the shared device lock (`/tmp/ultron-xiao.lock`) because
the XIAO is the only board and T12/T15 need it too. macOS has no `flock`-style
`-c`; use `lockf -k <file> <command>`. **Before flashing, confirm the chip
answers** — a silent board is hung, not busy (see the blocker section).

```bash
# 0) is the board alive at all? (expect "commands: rec <label> | ls | get <name> | help")
lockf -k /tmp/ultron-xiao.lock python3 devices/esp32/tools/xiao_pull.py ls

# 1) compile + flash the recorder (T28 fixed the PDM.h blocker)
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/wav_recorder
lockf -k /tmp/ultron-xiao.lock arduino-cli upload -p /dev/cu.usbmodem2101 \
    --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/wav_recorder

# 2) record 3 takes per trained label, pulled into /tmp/xiao_takes/<label>/
lockf -k /tmp/ultron-xiao.lock python3 devices/esp32/tools/xiao_pull.py session \
    --labels wake,noise,la,lb --takes 3 --out /tmp/xiao_takes

# 3) ingest the device takes, tagging the mic source (one ingest per label)
#    --dest is the dataset ROOT: clips land in dataset/originals/<label>/
#    (a --dest .../originals path would nest a second originals/ level)
for label in wake noise la lb; do
  uv run ultron dataset ingest /tmp/xiao_takes/$label $label --mic xiao --dest dataset
done

# 4) validate — must exit 0 and list every checked file
uv run ultron dataset validate dataset

# 5) DC offset: `DBG mean=` from the recorder says whether correction is needed
#    (subtracts the per-clip mean in place; the clips stay xiao-tagged)
uv run ultron dataset dcfix dataset

# 6) DSP parity on the real device audio (skips when no xiao clip is present)
uv run pytest tests/test_dsp_parity.py -q -s -k device
```

Expected shape of the output (the numbers are filled in by the run, never by
hand — that is the point of the pipeline):

```
$ uv run ultron dataset ingest /tmp/xiao_takes/la la --mic xiao --dest dataset/originals
ingested 3 wav(s) into dataset/originals/la
$ uv run ultron dataset validate dataset
checked 12 wav(s) under dataset
all wavs valid (16 kHz / 16-bit / mono)
$ uv run pytest tests/test_dsp_parity.py -q -s -k device
  la_xiao_01.wav                  250   <max abs diff>
  …
  TOTAL                          3000   <worst>        # gate: ≤ 1e-3
```

### Measured state of the dataset before this pass (2026-11-21)

Counted on this machine (repo `dataset/` is gitignored and was **empty**, so the
training data lives outside the repo):

| Location | Clips | Labels | Mic tags |
|---|---|---|---|
| `~/dataset/originals/` | 63 | `wake` 11, `noise` 6, `la` 13, `lb` 11, `a` 20, `cc` 2 | **all `local`** (host mic) — **no `xiao`, no `pi` clip exists yet** |
| `~/dataset/originals/originals/` | 22 of the above | nested duplicate tree (`a` 20, `cc` 2) | `local` |
| `~/dataset/takes/` | 16 | long takes (`take_01_la.wav`, …) — never a training source | none |
| `~/Music/…/Unknown Album/` | 20 | stray `ultron record` output (`la_local_01.wav`, `wake_local_05.wav`, an unregistered `apaga.wav`, one 44.1 kHz stereo `vocals.wav`) | `local` |

Every clip in `~/dataset/originals` is 16 kHz / 16-bit / mono (the format
contract holds); what is missing is **device audio**. Gap to NFR-005 for the
trained subset: `wake` 11/30, `noise` 6/30, `la` 13/40, `lb` 11/40 originals, and
0 of them from the XIAO. Model state is reported, not fixed, here: the committed
training report on this machine shows a fresh hold-out accuracy of **0.25** with
every sample predicted `la` (confusion columns all in the `la` column,
`build/train_report.json`) — a spec 002 problem, out of T28's scope.

| Check | What it proves |
|---|---|
| `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/wav_recorder` | the sketch builds on the project toolchain — the `PDM.h` blocker is fixed (360001 B flash / 23352 B globals, no sketch warnings with `--warnings all`) |
| `uv run pytest tests/test_xiao_pull.py -q` | the pull tooling validates formats and its CLI surface (no hardware needed) |
| `uv run pytest tests/test_dsp_parity.py -q` | MFCC parity stays green (`2 passed`); the device-recording case skips while no `xiao` clip exists |
| **not run** | the on-device takes, `ultron dataset validate` on device WAVs, and the device parity numbers — blocked, see below |

### Blocked on hardware (device hung before the first take)

The first upload in this session **succeeded** (the recorder image was written and
the board reset). The following serial read returned 0 bytes and every later
upload fails:

```
$ arduino-cli upload -p /dev/cu.usbmodem2101 --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/wav_recorder
esptool v5.3.1
Serial port /dev/cu.usbmodem2101:
Connecting......................................
A fatal error occurred: Failed to connect to ESP32-S3: No serial data received.
```

Diagnosis: the chip is hung while its USB-Serial-JTAG peripheral stays powered —
the device still enumerates (`ioreg`: *USB JTAG_serial debug unit*, `0x303A:0x1001`,
USB Address 1, directly on a root port), but it produces no bytes and never
re-enumerates, so no reset is being applied. Software recovery was exhausted:
`esptool` with `--before default-reset | usb-reset | no-reset`, `--no-stub`,
`--connect-attempts`, `/dev/cu.*` **and** `/dev/tty.*`, plus manual DTR/RTS
toggle sequences — all `No serial data received`. There is no passwordless
`sudo`, the board is not behind a switchable hub, and an agent cannot press the
board.

**Owner recovery (2 s):** press **RESET (EN)** on the XIAO, or unplug the USB-C
cable and plug it back in. Then confirm and re-run step 1–6 above:

```bash
lockf -k /tmp/ultron-xiao.lock python3 devices/esp32/tools/xiao_pull.py ls
```

If `sd_begin_failed` comes back after the reset, the microSD card is missing or
the CS pin differs on this board revision: the sketch pins it at `GPIO10`
(unverified on hardware — the card was never reached). The candidate pins probed
in bring-up were `3, 4, 5, 7, 10, 21, 43, 44`; the card slot is the only SPI
device, so any working CS is a one-line change in
`devices/esp32/wav_recorder/wav_recorder.ino`. Run `xiao_pull.py mic` first to
tell a dead card apart from a dead mic (`ERR mic_begin_failed` vs
`ERR sd_begin_failed`).

### Findings worth keeping

- **`PDM.h` is gone in core 3.3.12** and the XIAO needs no downgrade: PDM RX via
  `ESP_I2S` (`I2S_MODE_PDM_RX`) works on ESP32-S3 and needs no extra library.
  This is the capture path T12 should adopt (same pins, same 16-bit mono output).
- **The recorder's SD paths were wrong.** The SD library mounts the FAT volume at
  `/sdcard`, and `VFSImpl::open()` prefixes the mountpoint *without* stripping it,
  so the old `"/sdcard/<name>.wav"` would have resolved to
  `/sdcard/sdcard/<name>.wav`. The sketch now uses `/<name>.wav` and only the
  printed path keeps the mountpoint prefix for humans.
- **No card-reader step is needed** for this pass: `get` streams the WAVs over the
  same USB port, so the pass is reproducible from the host (and by an agent)
  without touching the hardware.
- **`ultron dataset ingest --dest` was taking the dataset root, not the originals
  directory** — the CLI default said `dataset/originals` while the library appends
  `originals/<label>`, so the default invocation produced
  `dataset/originals/originals/<label>/`. That is where the stray
  `~/dataset/originals/originals/{a,cc}` tree came from (a second ingest with the
  old default splits a label across two directories; the trainer and
  `dataset validate` still walk both, so nothing failed loudly). Fixed in the CLI
  default + docs, pinned by `tests/test_dataset.py`; the correct call is
  `--dest dataset`.
- **The dataset is far below NFR-005 and has no device audio at all** (see the
  table above) — recording volume is an owner decision and spec 002 work, not
  something this pass may paper over.


## Labels & mic tags

Files produced by these recorders are ready for `ultron dataset ingest`:
- XIAO: `dataset/originals/<label>/<label>_xiao_<nn>.wav`
- Pi:   `dataset/originals/<label>/<label>_pi_<nn>.wav`
- local: `dataset/originals/<label>/<label>_local_<nn>.wav`
- ext:  owner-supplied (phone/PC) recordings, tagged `ext`

NFR-005: ≥ 20 originals per accepted phrase variant, recorded from all mics (xiao, pi, local).

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
