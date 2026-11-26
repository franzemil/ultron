# Audio feedback — "Escuchando" + "comando"

Spec 003-can-act, task **T14** (FR-018, FR-019, FR-037, NFR-010, US-007, D-006, Q-012).
The XIAO speaks exactly two things: **"Escuchando"** when it accepts the wake word, and
**"Comando"** when it emits a command event. Nothing else is ever played, and nothing is
synthesized at runtime.

The **UltronVoice library owns the timing**: `Options.wakeClipMs` / `commandClipMs` arm the
playback interval inside the library's event sink, so `CONFIRMING` lasts exactly the clip and
the 5 s command window starts at `playback_end_ms`; the interval also makes the windows that
heard a clip non-deciding (FR-019/FR-037). This layer (`feedback.{h,cpp}` + the two hooks in
`ultron_voice.ino`) owns the **player** and the speaker's boot check.

## Wiring — XIAO ESP32S3 Sense → MAX98357A → speaker

Hardware-validated on 2026-11-20 (`devices/esp32/spike_audio`), the same path T14 uses.

```text
 XIAO ESP32S3 Sense                         MAX98357A I2S DAC/amp
 ───────────────────                        ────────────────────
 D2 (GPIO3)  BCLK ────────────────────────► BCLK
 D3 (GPIO4)  LRC  ────────────────────────► LRC
 D4 (GPIO5)  DIN  ────────────────────────► DIN
 5V (VBUS)   ─────────────────────────────► VIN      (Q-014: amp on 5 V VBUS)
 GND         ─────────────────────────────► GND      (common ground bus)
                                            GAIN ── not connected (9 dB)
                                            SD   ── not connected (enabled)
                                            OUT+ ─┐
                                            OUT− ─┴──► speaker, 4–8 Ω, 3 W
```

| Rule | Why |
|---|---|
| Amp **VIN on 5 V VBUS**, never a GPIO | Peak current at 3 W exceeds any GPIO source; the `power_probe` spike only proved a bench experiment (Q-014 resolved 2026-11-21) |
| **GAIN floating** | 9 dB default gain; the clips are peak-normalised to −1 dBFS |
| **SD floating** | Amp enabled; pulling SD low mutes it |
| Speaker 4–8 Ω, ≥ 3 W | A 4 Ω load gives ~3 W at 5 V; 8 Ω is quieter and cooler |
| Keep DIN away from the PDM mic | GPIO40/41 are the microphone's; DIN is the noisy line |

### I2S peripheral split (do not change)

The ESP32-S3 has **two** I2S controllers:

| Port | Owner | Mode |
|---|---|---|
| `I2S_NUM_0` (I2S0) | PDM microphone — UltronVoice `src/pdm_capture.h` | `I2S_MODE_PDM_RX` |
| `I2S_NUM_1` (I2S1) | MAX98357A speaker — `devices/esp32/ultron_voice/feedback.cpp` | `I2S_MODE_STD` TX |

Both pass the port explicitly. `I2S_NUM_AUTO` would silently take whichever port is free and
break the other path.

## The clips

Both files are rendered once, committed, and played **byte-identically** by every runtime
(the Orange Pi in spec 005 plays these same WAVs through its USB audio dongle). No target
performs runtime TTS (D-006).

| Asset | Spoken | Voice / rate | Duration | Budget | Flash cost |
|---|---|---|---|---|---|
| `assets/escuchando.wav` | "Escuchando" | Mónica (es_ES), 300 wpm | **441 ms** (7 058 samples) | ≤ 500 ms | 14 116 B |
| `assets/comando.wav` | "Comando" | Mónica (es_ES), 340 wpm | **256 ms** (4 094 samples) | ≤ 300 ms | 8 188 B |

Format: **16 kHz / 16-bit / mono PCM**, silence-trimmed at −45 dBFS, peak-normalised to
−1 dBFS, 4 ms fade at both edges (no clicks). `assets/README.md` records the exact render
commands; `scripts/render_feedback_assets.sh` reproduces them and fails if a clip lands
outside its budget.

### Regenerating

```sh
./scripts/render_feedback_assets.sh             # macOS say + ffmpeg → assets/*.wav
uv run python scripts/gen_feedback_clips.py     # WAVs → ultron_voice/clips.h
./scripts/deploy_xiao.sh --compile-only         # arduino-cli --libraries + build
```

`clips.h` is generated **and committed** (an `arduino-cli compile` on a clean checkout must
not need `python` or `say`). `tests/test_feedback_clips.py` re-renders it from the committed
WAVs, fails on drift, and checks that the sketch arms the library from the clip metadata
instead of a literal. The header also carries each WAV's sha256.

## Playback path

`devices/esp32/ultron_voice/feedback.{h,cpp}`:

- Streams the clip **straight out of flash** — no clip buffer is copied into RAM.
- **Mono, `I2S_SLOT_MODE_MONO`**: the ESP32-S3 transmits the same sample in both slots
  (hardware `tx_mono` + `tx_chan_equal`), so the MAX98357A in its default `(L+R)/2` mode
  plays full level without a duplication loop.
- Writes **static 480-byte chunks** (240 frames = 15 ms). Every playback buffer is `static` —
  never stack-allocated.
- The player creates its **own I2S TX channel** with a **16 × 240-frame (240 ms) DMA queue**
  and feeds at most **180 ms ahead** of the audible position. It cannot use `I2SClass`:
  `UltronVoice::poll()` performs one 1600-sample microphone read per call (~100 ms blocking)
  plus a classification pass every 500 ms, while `I2SClass`'s TX queue is fixed at
  6 × 240 frames = **90 ms** — shorter than one poll, so any clip would underrun with audible
  gaps. Radio silence is not an option either: the app loop is the only place the player can
  be serviced.
- Consequence: writes never fill the queue (≤ 180 ms of 240 ms), so they never block the app
  loop, capture/inference/`DBG pred=` keep running during playback (FR-019), and the clip
  ends at **start + duration**. The first descriptor plays within ~15 ms of the first write.
- RAM cost: 7 680 B of internal DMA RAM + 480 B static chunk (not part of the inference path).

## Trigger and timing

| Edge | Clip | Who does what |
|---|---|---|
| `WAKE_LISTEN → CONFIRMING` (the edge that emits `EVT WAKE`) | "Escuchando" | the library arms `wakeClipMs` on `evt->t_ms`; `onWake()` calls `feedback_play(kFeedbackEscuchando, ev.tMs)` |
| `LISTENING → WAKE_LISTEN` (the edge that emits `EVT CMD`) | "Comando" | the library arms `commandClipMs`; `onCommand()` calls `feedback_play(kFeedbackComando, ev.tMs)` before the relay work |

The app never calls `ultron_sm_playback_start()`: two arming paths would double-count the
interval and break NFR-010. Both hooks use `ev.tMs` (the timestamp the library armed with),
never a fresh `millis()`, so the clip's audible end is the same instant as `playback_end_ms`.

| Metric | Budget | How it is met |
|---|---|---|
| playback start after acceptance | ≤ 200 ms | the clip starts inside the event callback, on `ev.tMs` |
| "Escuchando" duration | ≤ 500 ms | committed asset 441 ms; the library armed 441 ms |
| "comando" duration | ≤ 300 ms | committed asset 256 ms; the library armed 256 ms |
| 5 s window origin | playback end | library: `finish_confirming()` takes `playback_end_ms` |
| wake entry (end of utterance → CONFIRMING) | ≤ 1.5 s (FR-001) | unchanged library path + one clip |

The visible evidence is four debug lines (same clock):

```text
EVT WAKE conf=0.94 t_ms=10441
DBG play=escuchando state=start t_ms=10441
DBG play=escuchando state=end t_ms=10882
DBG pred=… state=LISTENING …    (the library's window now counts from 10882)
```

`state=end` is emitted when the last queued sample has drained (`last_write + 180 ms`), which
is the same instant the library uses as the window origin.

## Self-hearing guard (FR-019, FR-037)

The speaker's own clip reaches the PDM mic, so no classification window may decide while its
frames overlap a playback interval. That rule lives in the **library** (`ultron_state_machine.c`)
and is what `wakeClipMs` / `commandClipMs` feed:

- while `CONFIRMING`, windows return without deciding (inference and `DBG pred=` keep running);
- a window whose captured frames `[window_start_ms, now_ms]` intersect the last playback
  interval `[playback_start_ms, playback_end_ms)` is **discarded**, and the interval is checked
  even after playback finished — the 2 s analysis window still contains the clip's frames for
  up to 2 s after it ends;
- the interval that triggers a wake is not retroactively discarded (it earned the wake).

A command spoken right after the clip is accepted as soon as a window no longer contains the
clip: with the 2 s window / 500 ms hop geometry, that is the first window starting after
`playback_end_ms`.

## Volume

- Clips are peak-normalised to **−1 dBFS**; the amp runs at its default **9 dB** gain
  (GAIN floating).
- Louder, without touching the assets: tie **GAIN → VDD** (15 dB) or **GAIN → GND** (12 dB);
  quieter: **GAIN → GND via 100 kΩ** (6 dB) or **GAIN → VDD via 100 kΩ** (3 dB).
- Do not re-render the clips to change volume — the assets are shared with the Pi (spec 005)
  and must stay byte-identical across targets.

## Fail-fast: `SPK_INIT_FAILED`

`audioBegin()` runs right after `voice.begin(options)`. If `feedback_begin()` cannot start
I2S1 (driver error, port conflict, unusable output), the app prints

```text
DBG spk_init=0 i2s=1 pins=3/4/5
EVT ERROR code=SPK_INIT_FAILED
DBG halted=1 — reboot to retry
```

and `loop()` stays halted (the relay outputs remain OFF — external pull-ups held them OFF
since reset, and `outputsBegin()` still runs its explicit OFF). The device never accepts wakes
it could not confirm audibly. The line is the same contract text the library's own `fail()`
path produces; the library's formatter is not reachable from the app, so the sketch prints it.

On success the boot prints:

```text
DBG spk_init=1 i2s=1 pins=3/4/5 gain=floating sd=floating vin=5v_usb
DBG audio clip=escuchando samples=7058 ms=441 budget_ms=500 rate=16000
DBG audio clip=comando samples=4094 ms=256 budget_ms=300 rate=16000
```

**How to exercise the failure path on the bench** (I2S cannot detect an unplugged amp — TX is
one-way), build the documented fault-injection variant:

```sh
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
  --libraries devices/esp32/libraries \
  --build-property "compiler.cpp.extra_flags=-DULTRON_FAULT_NO_SPK=1" \
  devices/esp32/ultron_voice
```

`feedback_begin()` then reports failure before touching the driver and the boot produces the
`SPK_INIT_FAILED` lines above. Flash the normal build afterwards.

## Flash and bench notes

```sh
./scripts/deploy_xiao.sh --compile-only          # CI: no port touched
./scripts/deploy_xiao.sh --selftest --compile-only
./scripts/deploy_xiao.sh --selftest --listen 60  # flash + capture (needs a live board)
lockf -k /tmp/ultron-xiao.lock /bin/sh -c './scripts/deploy_xiao.sh --lock --selftest --listen 60'
```

The XIAO is shared between workers: take the device lock (`lockf`) around flashing **and**
serial capture. `scripts/wake_feedback_test.py` is a host-side acceptance oracle for this
feature (it speaks "Escuchando" from the Mac on every `EVT WAKE` line) but it is not the
product path.

The committed model is T12's placeholder (random weights) until spec 002 ships the trained
artifact, so a spoken "hey ultron" is not recognizable on this build. The event/playback chain
is verified with the scripted-score build (`deploy_xiao.sh --selftest`), which drives the
**same** library, the **same** arming interval and the **same** player; the clip is real audio.
