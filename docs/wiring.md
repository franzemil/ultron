# Bench wiring — XIAO ESP32S3 Sense (spec 003, Milestone 3)

Source of truth: `.ai/sdd/specs/003-can-act/design.md` → "Relay hardware (2 channels in this
milestone)" and `.ai/sdd/specs/003-can-act/requirements.md` → "Constraints". This document is the
bench checklist for **what this milestone actually wires**: the XIAO + MAX98357A audio path and
**2 relay channels driven directly from the XIAO** (LM, LB). The 9-channel companion stack is
**not implemented here** — it is preserved for spec 006-can-act-full at the bottom of this page.

> **Do not adopt the scaffold map from the open `feat/esp32-scaffold-relays` PR.** That branch
> proposes a *temporary* T15 scaffold (IN1 = lm on D5, IN2 = **gd** on D6/GPIO43, companion UART
> moved to D8/D9) and edits spec 003 to match. Those spec edits are not part of the approved spec
> 003; this milestone implements the approved map below (LM on D5/GPIO6, **LB** on D8/GPIO7).
> A textual conflict with that PR on this file is expected and is the owner's to resolve.

## 1. What this milestone wires

```
══════ XIAO ESP32S3 SENSE — VOICE (PDM mic is on the Sense board, no wiring) ════
 USB-C ◄── power + console (EVT / DBG pred= / DBG out= / DBG tick_ms=)
 5V (VBUS) ──► MAX98357A VIN        (Q-014: amp on VBUS 5 V, not a GPIO)
 D2 (GPIO3)  I2S BCLK ──► MAX98357A BCLK
 D3 (GPIO4)  I2S LRC  ──► MAX98357A LRC
 D4 (GPIO5)  I2S DIN  ──► MAX98357A DIN
 MAX98357A OUT+/OUT− ──► speaker (4–8 Ω, 3 W); GAIN + SD unconnected (9 dB, enabled)

══════ 2 direct relay channels on XIAO spare pins (this milestone) ════
 D5 (GPIO6) ──► relay IN1 (LM)     3V3 ──► relay VCC (opto side)
 D8 (GPIO7) ──► relay IN2 (LB)     External 5 V ──► relay JD-VCC (jumper removed)
                                   XIAO GND ──► relay GND (common GND bus)
                                   Pull-ups to 3V3 on IN1/IN2 (active-LOW = OFF at boot)
```

### XIAO pin map

| Function | XIAO pin | GPIO | Direction | This milestone |
|---|---|---|---|---|
| PDM mic clock | Sense board | 40 | in (I2S0 PDM RX) | wired on the Sense expansion |
| PDM mic data | Sense board | 41 | in (I2S0 PDM RX) | wired on the Sense expansion |
| I2S BCLK → MAX98357A | D2 | 3 | out (I2S1) | wired (T14, validated 2026-11-20) |
| I2S LRC → MAX98357A | D3 | 4 | out (I2S1) | wired (T14) |
| I2S DIN → MAX98357A | D4 | 5 | out (I2S1) | wired (T14) |
| Relay IN1 — **LM** (luz media) | D5 | 6 | out, **active-LOW** | **wired (T15)** |
| Relay IN2 — **LB** (luz baja) | D8 | 7 | out, **active-LOW** | **wired (T15)** |
| UART link to companion (spec 006) | D6/D7 | 43/44 | — | **unconnected** in this milestone |
| LA, GD, GI, LPB, LPA, A, CC | — | — | — | **not wired** (spec 006); firmware logs `state=unwired` |

The same map lives in firmware as data: `devices/esp32/ultron_voice/pins.h`
(`gpio`, `active_low`, `wired` per channel). The firmware prints it at boot:

```
DBG out_pins wired=2/9 lm=gpio6:alow lb=gpio7:alow la=unwired gd=unwired gi=unwired
                       lpb=unwired lpa=unwired a=unwired cc=unwired
DBG out_registry commands=11 trained=2 unknown_behavior=0 unknown_channels=0
DBG out=lm state=off t_ms=…     ← explicit OFF at boot, both wired channels
DBG out=lb state=off t_ms=…
```

### 50 ms tick (FR-009)

`UltronVoice::poll()` performs one blocking ~100 ms microphone read, so the relay tick does **not**
ride the classification loop: `relay_gpio.h` starts a 50 ms `esp_timer` and serialises it with the
application's command handling through a mutex. The timer never prints — the engine queues each
channel change with the exact tick timestamp and the loop drains them into `DBG out=` lines, so the
console stays single-threaded for those lines:

```
DBG out=lm state=on t_ms=20014
DBG outcmd id=lb behavior=steady acted=1 on=0x03 t_ms=20014
DBG tick_ms n=400 min=49 max=61 avg=50 on=0x03 t_ms=40016
```

### Power rules (non-negotiable)

| Rule | Why |
|---|---|
| Relay **coils** on an external 5 V supply (`JD-VCC`), never USB VBUS | coil inrush would brown out the XIAO/amp; 9 coils ≈ 0.8 A worst case in spec 006 |
| **JD-VCC jumper removed** | coil switching noise stays off the logic/opto side |
| Relay `VCC` (opto side) from XIAO **3V3** | matches the 3.3 V logic level of IN1/IN2 |
| **Common GND bus**: XIAO, MAX98357A, relay board, external supply | opto inputs and UART (spec 006) need one reference |
| **Pull-ups to 3V3 on IN1/IN2** (10 kΩ typical) | the board is active-LOW: a floating input would switch the relay **ON** at boot |
| MAX98357A `VIN` from **5 V VBUS** (Q-014) | amp peaks cannot come from a GPIO |
| No SD card, no second supply on the logic side | clips live in flash; mic audio in RAM |

### Boot safety (NFR-009)

1. External pull-ups hold IN1/IN2 **HIGH = OFF** while the ESP32 is in reset (pins hi-Z).
2. `RelayGpioSink::init()` writes the output latch HIGH **before** switching the pin to an output,
   so enabling the driver cannot glitch a coil.
3. Every wired channel is explicitly driven OFF at boot and logged (`DBG out=… state=off`), so the
   serial log itself proves no channel was ON before the first `CMD`.
4. Relays only move on a command-driven edge: `OutputController` calls the pin layer only when a
   channel's desired state *changes*.
5. On a runtime fault (`EVT ERROR`) the sketch calls `OutputController::failSafe()`: every
   behaviour is dropped and every channel goes OFF instead of holding a stale command.

### Behaviour timings on this wiring

| Command | Channels on the bench | Behaviour |
|---|---|---|
| `lm` | LM | steady ON until another luz command |
| `lb` | **LM + LB** | steady ON |
| `la` | **LM + LB** (+ LA logged `unwired`) | steady ON |

The timing engines (`blink_45s`, `blink_until_cancel`, `cancel`, `motor_45s`, `pulse_2s`,
`pulse_05s`) ship in the same firmware but their channels are not wired in this milestone and the
trained 4-class model cannot emit those commands yet — they are proven by the host test
(`tests/esp32/outputs_engine_test.cpp`) and land on hardware in spec 006.

## 2. Preserved for spec 006 — companion ESP32-S3 SuperMini stack (**not implemented**)

The 9-channel map below is **not wired in this milestone**. It is kept here so the companion
bring-up starts from the approved spec rather than from a scaffold.

```text
══════ PRESERVED FOR SPEC 006 — companion ESP32-S3 SuperMini stack ════
 D6 (GPIO43) UART0 TX ──► SuperMini GPIO44 (RX)
 D7 (GPIO44) UART0 RX ◄─ SuperMini GPIO43 (TX, optional DBG)
 GPIO1 ►IN1 (LM)  GPIO2 ►IN2 (LB)  GPIO4 ►IN3 (LA)   GPIO5 ►IN4 (GD)
 GPIO6 ►IN5 (GI)  GPIO7 ►IN6 (LPB) GPIO8 ►IN7 (LPA)  GPIO9 ►IN8 (A)
 GPIO10 ►IN9 (CC)
```

| Rule (spec 006) | Why |
|---|---|
| Relays on GPIO1…GPIO10 | ESP32-S3 strapping pins 0/3/45/46 avoided |
| Companion drives the coils from its own 5 V, XIAO supplies only the opto side | keeps the XIAO's 3V3 rail clean |
| Companion parses the same `EVT` contract lines over UART | zero new protocol; same event semantics |
| Companion runs the *same* behaviour engines (`outputs.{h,cpp}`) | AGENTS rule 5 — no second implementation |

## 3. Both targets stay behaviour-identical (AGENTS rule 5)

The behaviour layer (`devices/esp32/ultron_voice/outputs.{h,cpp}`) knows **channel ids and
milliseconds only** — no GPIOs, no Arduino, no clock of its own. The pin layer is the only
target-specific part:

| Target | Pin layer | Milestone |
|---|---|---|
| XIAO ESP32S3 | `relay_gpio.h` — active-LOW `digitalWrite`, 50 ms `esp_timer` tick, `DBG out=` lines | this milestone |
| Companion ESP32-S3 SuperMini | its own `ChannelSink` over GPIO1…GPIO10 (UART-fed events) | spec 006 |
| Orange Pi Zero 2W | a `ChannelSink` over libgpiod | spec 005 |

Same registry (`model/registry.json` → the generated `ultron_config.h`), same tick (50 ms), same
timings, same event semantics on every target — any divergence in relay behaviour between targets
is a bug.

## 4. Bringing the bench up

```sh
# 1. Compile (no device needed). The voice pipeline is an Arduino library
#    (devices/esp32/libraries/UltronVoice), so --libraries is required.
arduino-cli compile --libraries devices/esp32/libraries \
  --fqbn esp32:esp32:XIAO_ESP32S3 devices/esp32/ultron_voice

# 2. Flash + capture the console — the XIAO is shared with the T12/T14 workers
./scripts/deploy_xiao.sh --lock --listen 45            # add --selftest for scripted EVT lines

# 3. Host proof for the behaviours this milestone cannot trigger by voice yet
uv run pytest tests/test_esp32_outputs.py -v
```

What to look for in the console:

- Boot: `DBG out_pins wired=2/9 …`, `DBG out_registry commands=11 …`, then
  `DBG out=lm state=off` + `DBG out=lb state=off` (no `state=on` before the first `EVT CMD`).
- `EVT CMD id=lb` → `DBG outcmd id=lb behavior=steady acted=1 on=0x03` and `DBG out=lb state=on`
  and `DBG out=lm state=on`.
- `EVT CMD id=la` → `DBG out=la state=unwired want=on` plus `DBG out=lb state=on`.
- Steady: `DBG tick_ms …` keeps printing with `on=0x03` and no further `DBG out=` lines.
- `EVT CMD id=la` is only reachable through `--selftest` until spec 006 trains the phrase; the
  scripted scores go through the real state machine and the real output controller.
