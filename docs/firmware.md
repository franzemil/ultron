# XIAO firmware — console-only core (spec 003 T12)

Everything the XIAO runs to turn "hey ultron" + a command phrase into console events,
without audio output and without relays. Audio feedback is T14, the 2 relay channels are
T15, and both are built on top of this tree.

- Sketch: `devices/esp32/ultron_voice/`
- Shared PDM capture: `devices/esp32/common/pdm_capture.h` (T14/T28 reuse it)
- Spec: `.ai/sdd/specs/003-can-act/` (requirements → design → tasks)
- Hardware: Seeed XIAO ESP32S3 **Sense** (PDM mic on the Sense expansion board)

## Pipeline

```
PDM mic (GPIO40 clk / GPIO41 data, I2S0)
   └─► 2 s int16 ring buffer @ 16 kHz, 500 ms hop          features.cpp     64 KB static
        └─► window DC-offset correction                     features_core.c  (numpy-equivalent)
             └─► shared C MFCC: 100 frames x 13 coefs       dsp/mfcc.c       (spec 001, not re-implemented)
                  └─► int8 quantization (scale/zero_point)  features_core.c
                       └─► TFLite-Micro, AllOpsResolver      infer.cpp        64 KB static arena
                            └─► 4-class scores (softmax in-graph)
                                 └─► WAKE_LISTEN / CONFIRMING / LISTENING   state_machine.c
                                      └─► USB serial: EVT lines + DBG pred= lines
```

Static buffers only: the ring buffer (64 KB), the TFLM arena (64 KB) and the feature/read
scratch live in `.bss`. Nothing allocates inside the inference loop (the shared MFCC library
used to `malloc()` one 40-float buffer per frame; that is a stack array now — same arithmetic,
`tests/test_dsp_parity.py` unchanged and green). `mfcc_init()`'s table allocation happens once
at boot and is reported in the `DBG mem` line.

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
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice
```

Verified with arduino-cli 1.5.1, esp32 core 3.3.12, `Chirale_TensorFLowLite` 2.0.0 and
`ESP_I2S` (bundled with the core). The TFLM runtime is the **raw TFLite-Micro API**
(`tensorflow/lite/micro/*`), not the `ArduTFLite` wrapper: that wrapper's
`modelSetInput(float, index)` only drives single-scalar models, while this one takes a
`[1, 100, 13]`-shaped int8 tensor.

### Self-test build (scripted scores, real event lines)

```sh
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
  --build-property "compiler.cpp.extra_flags=-DULTRON_SELFTEST=1" \
  devices/esp32/ultron_voice
```

`-DULTRON_SELFTEST=1` injects a scripted score sequence into the **same**
`ultron_sm_update()` the microphone path uses, so the `EVT WAKE` / `EVT CMD` / `EVT TIMEOUT`
lines are formatted by the shipping code. The boot banner prints `DBG selftest=1 … NOT_RECOGNITION`
and the interpreter keeps running, so `DBG pred=` and `infer_ms` stay real. It proves the
state machine and the contract on hardware; it does **not** prove recognition.

## Flash and capture (shared device)

The single XIAO is shared with the T14/T15/T28 workers. Take the lock for **both** flashing and
serial reads (macOS has no `flock`):

```sh
lockf /tmp/ultron-xiao.lock -c 'arduino-cli upload -p /dev/cu.usbmodem2101 \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice'

lockf /tmp/ultron-xiao.lock -c 'python3 - <<"PY"
import serial, time
s = serial.Serial("/dev/cu.usbmodem2101", 115200, timeout=1)
end = time.time() + 40
while time.time() < end:
    print(s.readline().decode("utf-8", "replace").rstrip())
PY'
```

After a flash, the next worker must first verify the chip answers before uploading anything. A
silent chip (port enumerates, zero bytes read, `esptool` cannot connect) is a stop-and-escalate
condition — never a retry loop.

## Regenerating after a retrain

```sh
uv run ultron export tflite                      # needs build/checkpoint.pt + build/features.npz
uv run python scripts/gen_esp32_config.py        # reads build/labels.json for the frozen order
uv run python scripts/gen_esp32_model.py         # embeds build/model_int8.tflite (placeholder=0)
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice
```

`gen_esp32_config.py --check` and `gen_esp32_model.py --check` fail when a committed generated
header is stale, so a threshold or class-order change cannot land without the firmware
following it.

## Tests (host, no hardware)

| Test | What it pins |
|---|---|
| `tests/test_esp32_state_machine.py` | the firmware's `state_machine.c` compiled on the host: FR-001…FR-008, FR-019, FR-037, Q-005, and the exact contract-line text |
| `tests/test_esp32_features.py` | `features_core.c` (DC correction + shared MFCC + int8 quantization) against the host pipeline — **byte-identical** int8 tensor |
| `tests/test_esp32_config.py` | generated `config.h` is current, complete, and threshold-free in the C sources |
| `tests/test_dsp_parity.py` | per-frame MFCC parity (unchanged by the firmware work) |
