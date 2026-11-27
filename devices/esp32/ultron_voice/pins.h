/*
 * pins — the XIAO ESP32S3 relay pin map (spec 003-can-act, T15).
 *
 * Approved wiring (design.md → "Relay hardware (2 channels in this milestone)"):
 *
 *   D5 (GPIO6) ──► relay IN1  (LM)      3V3 ──► relay VCC (opto side)
 *   D8 (GPIO7) ──► relay IN2  (LB)      External 5 V ──► relay JD-VCC (jumper removed)
 *                                        XIAO GND ──► relay GND (common GND bus)
 *                Pull-ups to 3V3 on IN1/IN2 (active-LOW = OFF at boot)
 *
 * Hardware rules the map encodes:
 *   - `active_low`  : the relay board input is active-LOW, so OFF = HIGH. With the
 *                     external pull-up the channel is OFF while the pin is an
 *                     input (boot/reset), and an explicit HIGH keeps it OFF once
 *                     the pin becomes an output (NFR-009).
 *   - `wired`       : only LM and LB exist on the bench in this milestone. Every
 *                     other contract channel is `wired = false`, so the pin layer
 *                     logs `DBG out=<id> state=unwired` instead of driving an
 *                     absent relay (spec 006 wires GD…CC on the companion).
 *
 * This file is *data*: no Arduino includes, so the map can be inspected (or
 * reuse-checked) by host tooling. The GPIO adapter lives in relay_gpio.h.
 *
 * Do not copy the `feat/esp32-scaffold-relays` scaffold map (IN1 = lm on D5,
 * IN2 = gd on D6/GPIO43, companion UART on D8/D9): that PR's spec edits are not
 * part of the approved spec 003 and are not implemented here.
 */

#ifndef ULTRON_PINS_H
#define ULTRON_PINS_H

#include <stdint.h>

#include "outputs.h"

namespace ultron {

struct PinSpec {
  int8_t gpio;     /* XIAO header GPIO number; -1 when the channel is unwired */
  bool active_low; /* relay input active-LOW (OFF = HIGH)                   */
  bool wired;      /* wired on the bench in this milestone                  */
};

/* Indexed by Channel (lm, lb, la, gd, gi, lpb, lpa, a, cc). */
constexpr PinSpec kChannelPins[kChannelCount] = {
    /* lm  */ {6, true, true},   /* D5 (GPIO6) → relay IN1 */
    /* lb  */ {7, true, true},   /* D8 (GPIO7) → relay IN2 */
    /* la  */ {-1, true, false}, /* spec 006 */
    /* gd  */ {-1, true, false}, /* spec 006 */
    /* gi  */ {-1, true, false}, /* spec 006 */
    /* lpb */ {-1, true, false}, /* spec 006 */
    /* lpa */ {-1, true, false}, /* spec 006 */
    /* a   */ {-1, true, false}, /* spec 006 */
    /* cc  */ {-1, true, false}, /* spec 006 */
};

/* Number of channels actually driven by this build (2 in this milestone). */
inline unsigned wiredChannels() {
  unsigned n = 0;
  for (unsigned i = 0; i < (unsigned)kChannelCount; i++) {
    if (kChannelPins[i].wired) n++;
  }
  return n;
}

}  // namespace ultron

#endif /* ULTRON_PINS_H */
