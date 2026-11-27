/*
 * outputs_config — glue between the generated command table and the output
 * engine (spec 003-can-act, T15).
 *
 * `ultron_config.h` is GENERATED from `model/registry.json` + `model/thresholds.json`
 * by `cli/src/ultron/esp32_config.py` and already carries `ULTRON_COMMANDS[]`: the
 * commands the model can emit today, each with its registry `behavior` and
 * `channels`. This header turns that table into the `ultron::CommandSpec[]` the
 * engine consumes, so the command → behaviour → channels mapping exists exactly
 * once (in the registry, through the generator) and never as a second
 * hand-written copy in C.
 *
 * Scope: `ULTRON_COMMANDS[]` is the *trained* subset (currently `la`, `lb`) — that
 * is the whole set of `EVT CMD` ids the state machine can produce. The engine
 * still implements every contract behaviour (blink, parqueo, wipers, pulses);
 * they start arriving as commands once spec 006's rollout trains them, at which
 * point the regenerated header grows and no C changes.
 *
 * Header-only: arduino-cli only compiles translation units inside the sketch
 * folder, and there is nothing here worth a .cpp.
 */

#ifndef ULTRON_OUTPUTS_CONFIG_H
#define ULTRON_OUTPUTS_CONFIG_H

#include <stddef.h>

#include "outputs.h"
#include "ultron_config.h"

namespace ultron {

/* Static backing store for the generated command table (no heap, NFR-002). */
struct RegistryCommandTable {
  CommandSpec specs[ULTRON_NUM_COMMANDS > 0 ? ULTRON_NUM_COMMANDS : 1];
  CommandTable table;
};

/* Fill `out` from the generated `ULTRON_COMMANDS[]`. Call once, from setup(). */
inline void loadRegistryTable(RegistryCommandTable *out) {
  for (size_t i = 0; i < (size_t)ULTRON_NUM_COMMANDS; i++) {
    out->specs[i].id = ULTRON_COMMANDS[i].id;
    out->specs[i].behavior = behaviorFromName(ULTRON_COMMANDS[i].behavior);
    out->specs[i].channels = channelsFromCsv(ULTRON_COMMANDS[i].channels);
  }
  out->table.items = out->specs;
  out->table.count = (size_t)ULTRON_NUM_COMMANDS;
}

/* Command entries whose `behavior` the engine cannot map — a config error the
 * firmware logs at boot instead of turning into a silent no-op at command time. */
inline size_t unknownBehaviors(const RegistryCommandTable &rt) {
  size_t n = 0;
  for (size_t i = 0; i < rt.table.count; i++) {
    if (rt.table.items[i].behavior == Behavior::kUnknown) n++;
  }
  return n;
}

/* Command entries whose `channels` CSV names no known channel (CP is the one
 * intentional empty list). Returns the first offending id through `first_bad`. */
inline size_t unknownChannels(const RegistryCommandTable &rt, const char **first_bad) {
  size_t n = 0;
  for (size_t i = 0; i < rt.table.count; i++) {
    const CommandSpec &spec = rt.table.items[i];
    if (spec.channels != 0) continue;
    if (spec.behavior == Behavior::kCancel) continue; /* CP drives no channel */
    if (n == 0 && first_bad != nullptr) *first_bad = spec.id;
    n++;
  }
  return n;
}

}  // namespace ultron

#endif /* ULTRON_OUTPUTS_CONFIG_H */
