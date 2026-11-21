/*
 * config_sm.h — glue between the generated config.h and the generic state machine.
 *
 * config.h carries only primitives + strings (it must stay valid in both C and
 * C++ and must not depend on other headers); state_machine.h owns the types.
 * This adapter assembles the `ultron_sm_config` the firmware and the host tests
 * both use, so the thresholds and the command table are read exactly once, from
 * the generated header.
 */

#ifndef ULTRON_CONFIG_SM_H
#define ULTRON_CONFIG_SM_H

#include "config.h"
#include "state_machine.h"

#if ULTRON_NUM_COMMANDS > ULTRON_MAX_COMMANDS
#error "config.h trains more commands than state_machine.h can hold (ULTRON_MAX_COMMANDS)"
#endif
#if ULTRON_NUM_CLASSES > ULTRON_MAX_CLASSES
#error "config.h has more classes than state_machine.h can hold (ULTRON_MAX_CLASSES)"
#endif

/* Fill `gates` (ULTRON_NUM_COMMANDS entries) and `cfg` from the generated header. */
static inline void ultron_config_sm(ultron_sm_config *cfg, ultron_cmd_gate *gates) {
  for (int i = 0; i < ULTRON_NUM_COMMANDS; i++) {
    gates[i].class_index = ULTRON_COMMANDS[i].class_index;
    gates[i].id = ULTRON_COMMANDS[i].id;
    gates[i].threshold = ULTRON_COMMANDS[i].threshold;
    gates[i].margin = ULTRON_COMMANDS[i].margin;
  }
  cfg->n_classes = ULTRON_NUM_CLASSES;
  cfg->class_names = ULTRON_CLASS_NAMES;
  cfg->wake_class = ULTRON_WAKE_CLASS;
  cfg->wake_threshold = ULTRON_WAKE_THRESHOLD;
  cfg->wake_margin = ULTRON_WAKE_MARGIN;
  cfg->n_commands = ULTRON_NUM_COMMANDS;
  cfg->commands = gates;
  cfg->window_ms = ULTRON_WINDOW_MS;
}

#endif /* ULTRON_CONFIG_SM_H */
