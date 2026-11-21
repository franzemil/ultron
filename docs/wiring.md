# Wiring — XIAO ESP32S3 Sense bench stack

The real bench wiring for the XIAO stack (Milestone 3, spec 003). Companion relays live on a
second ESP32-S3 SuperMini over UART (FR-038); the XIAO's pins are reserved for mic + speaker.
Two relay inputs are driven directly on the XIAO only as temporary T15 scaffolding.

## XIAO ESP32S3 Sense pin map

| XIAO pad | GPIO | Bench use |
|---|---|---|
| D0 | GPIO1 | spare |
| D1 | GPIO2 | spare |
| D2 | GPIO3 | I2S BCLK → MAX98357A (validated 2026-11-20) |
| D3 | GPIO4 | I2S LRC → MAX98357A |
| D4 | GPIO5 | I2S DIN → MAX98357A |
| D5 | GPIO6 | scaffold relay IN1 (active-LOW, pull-up to 3V3) — removed end of T15 |
| D6 | GPIO43 (U0TXD) | scaffold relay IN2 (active-LOW, pull-up to 3V3) — removed end of T15 |
| D7 | GPIO44 (U0RXD) | spare (console pad) |
| D8 | GPIO7 | UART TX → SuperMini GPIO44 (RX) |
| D9 | GPIO8 | UART RX ← SuperMini GPIO43 (TX) |
| D10 | GPIO9 | power_probe amp-VIN test (Q-014 open) |
| D11 | GPIO42 | spare |
| D12 | GPIO41 | Sense PDM mic data (on-board) |

On-board (no header wiring): PDM mic CLK = GPIO40; Sense SD card CS = GPIO10; native USB =
GPIO19/20. Strapping pins 0/3/45/46 are avoided.

## MAX98357A I2S (speaker)

| Signal | XIAO pin | MAX98357A pin |
|---|---|---|
| BCLK | D2 (GPIO3) | BCLK |
| LRC | D3 (GPIO4) | LRC |
| DIN | D4 (GPIO5) | DIN |
| VIN | 5V VBUS (baseline — Q-014 open) | VIN |
| OUT+/OUT− | — | 4–8 Ω, 3 W speaker |

GAIN + SD unconnected (9 dB, enabled). Validated on hardware 2026-11-20 (`spike_audio`).

## Scaffold relays (temporary, T15)

| Relay IN | XIAO pin | Note |
|---|---|---|
| IN1 | D5 (GPIO6) | active-LOW, pull-up to 3V3 — removed end of T15 |
| IN2 | D6 (GPIO43) | active-LOW, pull-up to 3V3 — removed end of T15 |

≤ 2 channels driven directly on the XIAO only during T15 bring-up; removed at the end of T15.
GPIO43 (D6) is the U0TXD console pad — occupied only temporarily.

## Companion UART (shipping, FR-038)

| Direction | XIAO pin | SuperMini pin |
|---|---|---|
| TX | D8 (GPIO7) | GPIO44 (RX) |
| RX (optional DBG) | D9 (GPIO8) | GPIO43 (TX) |

One-way events at 115200-8N1. Not D6/D7 — GPIO43/GPIO44 are the U0TXD/U0RXD console pads.

## SuperMini relay map (GPIO1…GPIO10 → IN1…IN9)

| SuperMini GPIO | Relay IN | Channel |
|---|---|---|
| GPIO1 | IN1 | LM |
| GPIO2 | IN2 | LB |
| GPIO4 | IN3 | LA |
| GPIO5 | IN4 | GD |
| GPIO6 | IN5 | GI |
| GPIO7 | IN6 | LPB |
| GPIO8 | IN7 | LPA |
| GPIO9 | IN8 | A |
| GPIO10 | IN9 | CC |

GPIO3 (strapping) is skipped. All inputs active-LOW.

## Power rules

| Rule | Why |
|---|---|
| Relay coils on external 5 V | 9 coils ≈ 0.8 A worst case — would brown-out USB power |
| JD-VCC jumper removed | Coil switching noise stays out of the logic side |
| Common GND bus (XIAO, SuperMini, MAX98357A, relay, supply) | UART + opto inputs need a shared reference |
| Pull-ups to 3V3 on IN1…IN9 | Floating inputs would turn relays ON at boot (NFR-009) |
| All channels OFF at boot | Actuation safety (NFR-009) |

## Console

USB-C console = native USB (GPIO19/20) — unaffected by the scaffold. D6/D7 are the U0TXD/U0RXD
pads (default UART0 TX/RX), not the USB console.

## Validated / open

- **Validated (2026-11-20):** MAX98357A I2S on D2/D3/D4 (GPIO3/4/5), GAIN/SD floating, clip
  streamed from flash — `devices/esp32/spike_audio`.
- **Open:** Q-014 — MAX98357A VIN power source (5 V VBUS vs GPIO 3.3 V). `power_probe` drives
  D10 (GPIO9) to test powering VIN from a GPIO; unresolved, do not assume an answer.
