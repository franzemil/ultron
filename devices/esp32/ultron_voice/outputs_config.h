/*
 * outputs_config — glue between the generated registry and the output engine
 * (spec 003-can-act, T15).
 *
 * `config.h` is GENERATED from `model/registry.json` (scripts/gen_esp32_config.py)
 * and already carries `ULTRON_REGISTRY[]`: every registry command with its
 * `behavior` enum and `channels` CSV. This header turns that table into the
 * `ultron::CommandSpec[]` the engine consumes, so the command → behaviour →
 * channels mapping exists exactly once (in the registry, through the generator)
 * and never as a second hand-written copy in C.
 *
 * The same glue is used by the firmware and by the host test
 * (tests/esp32/outputs_engine_test.cpp), which is what proves the engines against
 * the *real* registry rather than a test-only table.
 *
 * Header-only: arduino-cli only compiles translation units inside the sketch
 * folder, and there is nothing here worth a .cpp.
 */

#ifndef ULTRON_OUTPUTS_CONFIG_H
#define ULTRON_OUTPUTS_CONFIG_H

#include <stddef.h>

#include "ultron_config.h"
#include "outputs.h"

namespace ultron {

/* Static backing store for the registry-derived table (no heap, NFR-002). */
struct RegistryCommandTable {
  CommandSpec specs[ULTRON_REGISTRY_SIZE];
  CommandTable table;
};

/* Fill `out` from the generated registry. Call once, from setup(). */
inline void loadRegistryTable(RegistryCommandTable *out) {
  for (size_t i = 0; i < (size_t)ULTRON_REGISTRY_SIZE; i++) {
    const ultron_registry_entry &entry = ULTRON_REGISTRY[i];
    out->specs[i].id = entry.id;
    out->specs[i].behavior = behaviorFromName(entry.behavior);
    out->specs[i].channels = channelsFromCsv(entry.channels);
  }
  out->table.items = out->specs;
  out->table.count = (size_t)ULTRON_REGISTRY_SIZE;
}

/* Registry entries whose `behavior` the engine cannot map — a config error the
 * firmware logs at boot instead of turning into a silent no-op. */
inline size_t unknownBehaviors(const RegistryCommandTable &rt) {
  size_t n = 0;
  for (size_t i = 0; i < rt.table.count; i++) {
    if (rt.table.items[i].behavior == Behavior::kUnknown) n++;
  }
  return n;
}

/* Registry entries whose `channels` CSV names no known channel (CP is the one
 * intentional empty list). Returns the offending ids through `first_bad`. */
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
