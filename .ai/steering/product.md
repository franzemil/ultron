# Ultron — Product Vision

## Value proposition

Ultron is a small, always-listening **voice-actuation manager for a car**, built by a
Spanish-speaking maker. It wakes on **"hey ultron"** and executes short Spanish command phrases —
*"luz alta"*, *"guiñador derecho"*, *"parqueo"*, *"arranque"*, *"cierre centralizado"*… — driving
9 relay channels (LM, LB, LA, GD, GI, LPB, LPA, A, CC) with the exact timing behaviors the car
needs: steady lights with hierarchy, 45 s turn-signal blinking, hazard until canceled, wiper
motor outputs, and timed pulses for starter and central lock. Everything — wake detection,
command recognition, actuation — runs **on-device**, with no cloud dependency.

## The problem we solve

1. Voice assistants in Spanish with custom wake words require cloud accounts, subscriptions, and
   constant connectivity; privacy and latency both suffer.
2. The previous project (`lampara-aliz`) proved on-device keyword spotting works on a XIAO
   ESP32S3 via Edge Impulse, but locked the model pipeline to the EI platform. Ultron owns the
   whole pipeline: **local PyTorch training as the source of truth**, exported to TFLite.
3. The maker wants always-on voice on a **microcontroller** (XIAO ESP32S3 Sense) — no Linux SBC in
   the loop (owner decision 2026-11-26: Orange Pi Zero 2W removed from scope), so there is one
   runtime to tune and one behaviour to trust.

## Who it is for

- **The owner (single user)**: one Spanish-speaking maker, one vehicle (or bench rig), one
  deployed device.
- Secondary audience: the owner's future self and AI agents, who must be able to retrain,
  redeploy, and re-benchmark from this repo alone.

## How it works (user-visible)

1. Say **"hey ultron"** → device enters command-listening mode (5 s).
2. Say a command in either accepted form (*"luz media"* or *"enciende luz media"*) → device
   drives the mapped relay channels per the behavior contract — e.g. *"guiñador derecho"* blinks
   GD at 1 Hz for 45 s, *"parqueo"* blinks both sides until *"cancelar parqueo"*.
3. Say nothing in 5 s → device returns to wake-listening.

## What it is NOT (v1)

- **No general speech recognition / transcription** — it is a fixed-phrase classifier, not ASR.
  Rationale: on-device ASR on an MCU is out of reach and unnecessary for a fixed command set.
- **No cloud inference** — rationale: privacy + latency + the whole point of the project.
- **No multi-room / multi-device coordination** — exactly one device is deployed; the second
  candidate exists only during evaluation.
- **No multi-speaker voice enrollment** — trained primarily on the owner's voice (more speakers
  improve robustness, but none is required).
- **No WiFi/remote control features in v1** — actuation is local; networking is a v2 concern.
- **No multilingual support** — every command is Spanish (v1 hard constraint); "hey ultron" is a
  fixed acoustic token whose English spelling is irrelevant — only how the owner says it matters.
- **No runtime phrase editing** — changing the command registry requires retraining and
  redeploying; there is no "teach new phrase while running" mode.
- **Not a driving-safety system** — arranque (starter crank) only ever pulses ≤ 2 s and only via
  an explicit wake → command sequence; the device never asserts a channel without a command.
