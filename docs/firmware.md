# XIAO firmware — console-only core (spec 003 T12)

Everything the XIAO runs to turn "hey ultron" + a command phrase into console events,
without audio output and without relays. Audio feedback is T14, the 2 relay channels are
T15, and both are built on top of this tree.

- Sketch: `devices/esp32/ultron_voice/ultron_voice.ino` — **application logic only** (relays)
- Library: `devices/esp32/libraries/UltronVoice/` — the whole voice pipeline
- Shared PDM capture: `libraries/UltronVoice/src/pdm_capture.h` (T14/T28 reuse it; the old
  `devices/esp32/common/pdm_capture.h` is a one-line forwarding header)
- Spec: `.ai/sdd/specs/003-can-act/` (requirements → design → tasks)
- Hardware: Seeed XIAO ESP32S3 **Sense** (PDM mic on the Sense expansion board)

## Pipeline

```
PDM mic (GPIO40 clk / GPIO41 data, I2S0)
   └─► 2 s int16 ring buffer @ 16 kHz, 500 ms hop          features.cpp     64 KB static
        └─► window DC-offset correction                ultron_features_core.c (numpy-equivalent)
             └─► shared C MFCC: 100 frames x 13 coefs       dsp/mfcc.c       (spec 001, not re-implemented)
                  └─► int8 quantization (scale/zero_point) ultron_features_core.c
                       └─► TFLite-Micro, AllOpsResolver      infer.cpp        64 KB static arena
                            └─► 4-class scores (softmax in-graph)
                                 └─► WAKE_LISTEN / CONFIRMING / LISTENING   ultron_state_machine.c
                                      └─► USB serial: EVT lines + DBG pred= lines
```

Static buffers only: the ring buffer (64 KB), the TFLM arena (64 KB) and the feature/read
scratch live in `.bss`. Nothing allocates inside the inference loop (the shared MFCC library
used to `malloc()` one 40-float buffer per frame; that is a stack array now — same arithmetic,
`tests/test_dsp_parity.py` unchanged and green). `mfcc_init()`'s table allocation happens once
at boot and is reported in the `DBG mem` line.

## Layout: library vs. sketch

The voice pipeline is an **Arduino library**, not sketch code, so a sketch contains only the
application logic:

```
devices/esp32/libraries/UltronVoice/            ← all of this is "how we hear"
  library.properties
  src/UltronVoice.h, UltronVoice.cpp           ← the public surface: begin() / poll(Event)
  src/ultron_state_machine.{h,c}               ← WAKE_LISTEN / CONFIRMING / LISTENING
  src/ultron_features.{h,cpp}                  ← 2 s ring, 500 ms hop
  src/ultron_features_core.{h,c}               ← DC fix, MFCC, int8 quantize (portable C99)
  src/ultron_infer.{h,cpp}                     ← TFLite-Micro, AllOpsResolver, 64 KB arena
  src/pdm_capture.h                            ← shared I2S PDM RX capture (I2S0)
  src/mfcc.{c,h}                               ← vendored copy of dsp/mfcc.c (drift-guarded)
  src/ultron_config.h                          ← GENERATED: thresholds + registry subset
  src/model_data.h                             ← GENERATED: flatbuffer bytes
  src/ultron_selftest.h                        ← scripted scores (plumbing proof)

devices/esp32/ultron_voice/ultron_voice.ino     ← application logic only
```

The sketch is ~110 lines of relay logic:

```cpp
#include <UltronVoice.h>
UltronVoice voice;

void setup() {
  pinMode(kRelayLm, OUTPUT); pinMode(kRelayLb, OUTPUT);   // active-LOW, OFF at boot
  Serial.begin(115200);
  voice.begin();                                          // prints the boot banner
}

void loop() {
  UltronVoice::Event ev;
  if (!voice.poll(ev)) return;
  if (ev.type == UltronVoice::Type::Command) {
    if (!strcmp(ev.id, "lb")) { /* LM + LB on */ }
  }
}
```

`poll()` performs one 100 ms microphone read and returns `true` at most once per 500 ms window,
carrying one `Event { Wake | Command | Timeout | Error }` (`id`, `conf`, `tMs`). The library
prints the `EVT`/`DBG` lines itself on the `Stream` you pass in `Options.console` (default
`Serial`), so the contract is identical no matter which sketch uses it.

`Options` is also where T14 plugs audio in: `wakeClipMs = 500` / `commandClipMs = 300` make the
library arm the playback interval **on the event edge**, so `CONFIRMING` lasts exactly as long as
the clip and the 5 s window starts at its end (FR-019/NFR-010) — the sketch only has to play the
audio when it receives the event.

An Arduino library must be self-contained (arduino-cli builds only the library's own `src/`), so
`src/mfcc.c` is a **vendored copy** of `dsp/mfcc.c`. `scripts/sync_ultronvoice.py --check` fails
on any drift and `tests/test_esp32_config.py` runs it, so the firmware cannot silently run an old
MFCC while the host pipeline moved on.

## Event contract (design.md → "Event contract")

| Event | Line |
|---|---|
| Wake | `EVT WAKE conf=0.94 t_ms=12345` |
| Command | `EVT CMD id=lb conf=0.91 t_ms=12900` |
| Timeout | `EVT TIMEOUT t_ms=16000` |
| Error | `EVT ERROR code=MIC_INIT_FAILED` |

`t_ms` is milliseconds since boot; `id` is the registry id (`la`, `lb`, … — see
`model/registry.json`). One `DBG pred=` line per classified window (NFR-008) carries the
argmax, its confidence and the inference latency:

```
DBG pred=noise conf=0.7712 state=WAKE_LISTEN t_ms=6543 infer_ms=41 win_ms=118
```

`infer_ms` is the TFLM `Invoke()` alone; `win_ms` is the whole NFR-001 window budget
(MFCC + DC correction + quantization + inference). NFR-001's ≤ 400 ms applies to `win_ms`.

`EVT` lines are the contract — actuators and the spec-004 bench parse only those. Error codes
currently emitted: `MIC_INIT_FAILED`, `MIC_READ_FAILED`, `MFCC_INIT_FAILED`,
`MODEL_INIT_FAILED`, `MODEL_CLASS_MISMATCH`, `ARENA_TOO_SMALL`, `INFER_FAILED`.

## State machine

| Current | Trigger | Action | Next |
|---|---|---|---|
| WAKE_LISTEN | wake ≥ `wake + margin_wake` and argmax = wake | emit `WAKE`, arm the clip (T14) | CONFIRMING |
| WAKE_LISTEN | anything else | silent | WAKE_LISTEN |
| CONFIRMING | clip ends | start 5 s window; windows overlapping the clip are discarded | LISTENING |
| LISTENING | command ≥ `threshold + margin` **and** ≥ margin over every other class | emit `CMD`, stop the timer | WAKE_LISTEN |
| LISTENING | wake wins the window | refresh the 5 s timer **once** (Q-005) | LISTENING |
| LISTENING | 5 s elapsed | emit `TIMEOUT` | WAKE_LISTEN |

Thresholds and the command table are **never** in C sources: `config.h` is generated from
`model/thresholds.json` + `model/registry.json`, and a test fails if it drifts.

The console-only build arms no clip, so `CONFIRMING` lasts 0 ms and the 5 s window starts on
the same tick as the `WAKE` line. T14 arms the real "Escuchando" duration from inside the WAKE
sink (`ultron_sm_playback_start()` in the emit callback) — that contract is documented in
`state_machine.h` and exercised by `tests/test_esp32_state_machine.py`
(`window_starts_at_playback_end`).

Playback exclusion (FR-019/FR-037): a window whose frames overlap a clip interval is counted
and discarded — it never decides — while inference and `DBG pred=` logging continue.

## Capture: why I2S PDM RX (and not `PDM.h`)

ESP32 Arduino core **3.3.12** does not ship `PDM.h` (the ESP32 2.x API), so
`devices/esp32/wav_recorder/wav_recorder.ino` does not compile today:

```
wav_recorder.ino:23:10: fatal error: PDM.h: No such file or directory
```

The supported route on 3.3.12 is **I2S PDM RX** via `ESP_I2S.h`
(`I2S_MODE_PDM_RX`, `setPinsPdmRx()`), enabled because the ESP32-S3 defines
`SOC_I2S_SUPPORTS_PDM_RX (1)`. That is what `devices/esp32/common/pdm_capture.h` does:

| Item | Value |
|---|---|
| Pins | PDM data **GPIO41**, PDM clock **GPIO40** (Sense expansion mic) |
| I2S port | **I2S_NUM_0** (mic) — the MAX98357A speaker in T14 must use **I2S_NUM_1** |
| Format | int16 mono @ 16 kHz (`I2S_DATA_BIT_WIDTH_16BIT`, `I2S_SLOT_MODE_MONO`) |
| API | `ultron::PdmCapture::begin()` / `read(int16_t*, samples)` / `end()` |

It is header-only on purpose: arduino-cli only compiles sources that live in the sketch folder
(or its `src/`), so an out-of-sketch `.cpp` cannot be linked by a plain sketch build. `inline`
functions keep one shared source of truth that every sketch can `#include` relatively:

```cpp
#include "../common/pdm_capture.h"
```

## Model

`model_data.h` embeds the flatbuffer as `const unsigned char[]` for
`tflite::GetModel()`. It is generated, committed, and produced by:

```sh
uv sync --extra export                               # onnx2tf + tensorflow (heavy, one-off)
uv run python scripts/gen_esp32_placeholder.py       # deterministic PLACEHOLDER, see below
uv run python scripts/gen_esp32_model.py --placeholder
```

**What is embedded right now: a PLACEHOLDER, not the spec-002 artifact.**

The committed `model_data.h` contains the deterministic placeholder built by the repo's own
export machinery (`ultron_model.export.run_export`) from a **randomly initialized** `KwsNet`
— the same pattern as the T9 conversion smoke test. Reasons, in order:

1. `build/features.npz` does not exist on the build machine, so `ultron export tflite` cannot
   run against the real training set.
2. The existing `build/checkpoint.pt` was trained with an explicit `--min-originals` override
   on 6 originals/label (NFR-005 asks for 30–40) and reports **fresh hold-out accuracy 0.25**
   with every sample predicted `lb`. It is not usable.

The placeholder has the **real signature and the real class order** (`wake`, `noise`, `lb`,
`la`), so the only difference from the trained artifact is the bytes. The header records
`ULTRON_MODEL_PLACEHOLDER 1` plus a provenance line, and the device prints both at boot
(`DBG mem … placeholder=1`).

**What a placeholder proves:** the flatbuffer parses, `AllocateTensors()` fits the arena,
inference is fast enough, event/DBG formatting is right, and the state machine reacts
correctly to the scores it is given.

**What it cannot prove:** recognition. It will not classify "hey ultron" as `wake`, so
US-001 AC1 (real wake → real command) stays **blocked on the spec-002 artifact** — the
placeholder is explicitly not claimed as a pass. The event contract and state machine are
proven separately (host tests + the `ULTRON_SELFTEST` build).

### Ops in the flatbuffer

Measured on the committed placeholder with the TFLite interpreter: `CONV_2D`, `MAX_POOL_2D`,
`MEAN`, `FULLY_CONNECTED`, `RESHAPE`, `SOFTMAX`, `TRANSPOSE` (KwsNet's `Conv1d`/`MaxPool1d`
lower to their 2-D forms in the ONNX → TFLite path). All are TFLite builtins, so the
`AllOpsResolver` covers them; a future model that needs an op TFLM lacks fails loudly at
`AllocateTensors()` (`EVT ERROR code=MODEL_INIT_FAILED`), never silently.

### Input layout (measured, not assumed)

The exporter's docstring says the TFLite input comes back NWC `(1, 100, 13)`. On this machine
(onnx2tf **1.28.8**, tensorflow **2.21.0**) the int8 flatbuffer is **NCW `(1, 13, 100)`**.
The exporter already tolerates both (`export.py::_run_tflite` transposes), so the firmware
does the same: it detects the layout from the input tensor at boot and transposes the
coefficients when needed. Features are always produced in NWC order (100 frames × 13).

### Memory (NFR-002)

| Item | Value |
|---|---|
| int8 model (flash) | 9.3 KB placeholder (budget ≤ 100 KB) |
| TFLM arena | 64 KB reserved, `arena_used` logged at boot (ceiling ≤ 150 KB) |
| Audio ring buffer | 64 KB (2 s int16 @ 16 kHz) |
| MFCC tables | logged at boot (`mfcc=…B`, design ≤ 8 KB) |
| Static RAM (compiler) | **169 560 B** of 327 680 B (51 %) |
| Inference-path RAM | `arena + ring + io + mfcc`, logged as `inference_ram=` (budget ≤ 240 KB) |

The arena is 64 KB rather than the 150 KB ceiling: the design estimates 16–32 KB for this
architecture and the analytic activation bound is ~6 KB, so 64 KB keeps the inference path
inside the 240 KB budget with headroom. Raise it without touching code:

```sh
--build-property "compiler.cpp.extra_flags=-DULTRON_ARENA_KB=128"
```

A model that does not fit fails loud (`EVT ERROR code=ARENA_TOO_SMALL`).

## Build

```sh
arduino-cli compile --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice
```

Verified with arduino-cli 1.5.1, esp32 core 3.3.12, `Chirale_TensorFLowLite` 2.0.0 and
`ESP_I2S` (bundled with the core). The TFLM runtime is the **raw TFLite-Micro API**
(`tensorflow/lite/micro/*`), not the `ArduTFLite` wrapper: that wrapper's
`modelSetInput(float, index)` only drives single-scalar models, while this one takes a
`[1, 100, 13]`-shaped int8 tensor.

### Self-test build (scripted scores, real event lines)

```sh
arduino-cli compile --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 \
  --build-property "compiler.cpp.extra_flags=-DULTRON_SELFTEST=1" \
  devices/esp32/ultron_voice
```

`-DULTRON_SELFTEST=1` injects a scripted score sequence into the **same**
`ultron_sm_update()` the microphone path uses, so the `EVT WAKE` / `EVT CMD` / `EVT TIMEOUT`
lines are formatted by the shipping code. The boot banner prints `DBG selftest=1 … NOT_RECOGNITION`
and the interpreter keeps running, so `DBG pred=` and `infer_ms` stay real. It proves the
state machine and the contract on hardware; it does **not** prove recognition.

## Flash and capture

**One command** (compiles → preflight → flashes → optionally captures):

```sh
./scripts/deploy_xiao.sh                     # compile + flash
./scripts/deploy_xiao.sh --listen 40         # …and save 40 s of console to /tmp/ultron-xiao-logs/
./scripts/deploy_xiao.sh --selftest --listen 40   # scripted-score build (real EVT lines)
./scripts/deploy_xiao.sh --compile-only      # build only — port untouched, safe while others flash
./scripts/deploy_xiao.sh --dry-run           # print what it would run, execute nothing
```

The script encodes the rules that are easy to get wrong by hand:

1. **compile first** — a compile error never reaches the port;
2. **preflight read** — a port that enumerates but returns zero bytes is a *hung chip*, so it
   aborts (exit `1`) instead of uploading into it. A silent chip is a stop condition, never a
   retry loop;
3. **one failure message per stage**, with exit codes: `0` ok, `1` silent/hung, `2` usage,
   `3` compile failed, `4` upload failed, `5` no console output after flashing.

### Locking is opt-in

`--lock` holds `/tmp/ultron-xiao.lock` for the whole run. The lock only matters when more than
one worker touches the single board; **it is off by default**, and the script merely *reports*
when the file is currently held by someone else (`Whether you then proceed is your call`).
Use it when T14/T15/T28 are active:

```sh
./scripts/deploy_xiao.sh --lock --listen 40
# equivalent by hand (macOS lockf takes a command, not a string — there is no -c):
lockf -k /tmp/ultron-xiao.lock /bin/sh -c './scripts/deploy_xiao.sh --listen 40'
```

### By hand, if you prefer

```sh
# 1. build without touching the port
arduino-cli compile --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice

# 2. is the chip alive? (read-only; exit 1 = zero bytes = hung)
python3 scripts/serial_log.py --seconds 5 --quiet; echo "exit=$?"

# 3. flash (bootloader 0x0, partitions 0x8000, boot_app0 0xe000, app 0x10000)
arduino-cli upload -p /dev/cu.usbmodem2101 --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice

# 4. console
python3 scripts/serial_log.py --seconds 40            # timestamped
python3 scripts/serial_log.py --seconds 60 --evt-only  # contract lines only
python3 scripts/serial_log.py --seconds 40 --raw > /tmp/xiao.log
```

`scripts/serial_log.py` is stdlib-only (`termios`) because pyserial is not installed; it exits
non-zero when the port stays silent, so callers never mistake "no bytes" for "no events".
Interactive alternative when you are the only worker: `screen /dev/cu.usbmodem2101 115200`
(quit with `Ctrl-A K`) — but `screen` takes the port without any lock discipline.

## Regenerating after a retrain

```sh
uv run ultron export tflite                      # needs build/checkpoint.pt + build/features.npz
uv run python scripts/gen_esp32_config.py        # reads build/labels.json for the frozen order
uv run python scripts/gen_esp32_model.py         # embeds build/model_int8.tflite (placeholder=0)
arduino-cli compile --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice
```

`gen_esp32_config.py --check` and `gen_esp32_model.py --check` fail when a committed generated
header is stale, so a threshold or class-order change cannot land without the firmware
following it.

## Tests (host, no hardware)

| Test | What it pins |
|---|---|
| `tests/test_esp32_state_machine.py` | the library's `ultron_state_machine.c` compiled on the host: FR-001…FR-008, FR-019, FR-037, Q-005, and the exact contract-line text |
| `tests/test_esp32_features.py` | `ultron_features_core.c` + `mfcc.c` (DC correction + MFCC + int8 quantization) against the host pipeline — **byte-identical** int8 tensor |
| `tests/test_esp32_config.py` | generated `ultron_config.h` is current and complete, no thresholds in C sources, and the vendored `mfcc.c` is byte-identical to `dsp/` |
| `tests/test_dsp_parity.py` | per-frame MFCC parity (unchanged by the firmware work) |
