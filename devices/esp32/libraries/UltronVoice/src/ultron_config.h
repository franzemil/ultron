/*
 * config.h — GENERATED FILE, DO NOT EDIT.
 *
 * Source of truth: model/thresholds.json (spec 002 Q-006) + model/registry.json
 * (spec 001 T3), rendered by scripts/gen_esp32_config.py for spec 003 T12.
 * Re-render after any threshold/registry change:
 *
 *     uv run python scripts/gen_esp32_config.py
 *
 * CI guard: tests/test_esp32_config.py re-renders and fails on drift.
 *
 * config sha256 : 0636d6a6fd6a156d0107da13dca86ad030642432e97915eec6ee50aab8bcdbd2
 * class order   : wake noise lb la
 * order source  : derived: wake, noise, then trained commands in registry order
 * window        : 5.0 s
 */

#ifndef ULTRON_CONFIG_H
#define ULTRON_CONFIG_H

#define ULTRON_CONFIG_SHA256 "0636d6a6fd6a156d0107da13dca86ad030642432e97915eec6ee50aab8bcdbd2"
#define ULTRON_CLASS_ORDER_SOURCE "derived: wake, noise, then trained commands in registry order"

/* Frozen class order — the model's output index mapping (spec 002 TD-012). */
#define ULTRON_NUM_CLASSES 4
static const char *const ULTRON_CLASS_NAMES[ULTRON_NUM_CLASSES] = {"wake", "noise", "lb", "la"};

/* Wake stage (FR-001: wake >= threshold + margin). */
#define ULTRON_WAKE_CLASS 0
#define ULTRON_NOISE_CLASS 1
#define ULTRON_WAKE_THRESHOLD 0.8f
#define ULTRON_WAKE_MARGIN 0.1f

/* Command window (FR-002 / FR-004): 5 s, milliseconds on device. */
#define ULTRON_WINDOW_MS 5000u

/* Trained command subset (spec 002 TD-012) — 2 command(s). */
#define ULTRON_NUM_COMMANDS 2
static const struct {
  const char *id;      /* registry id — EVT CMD id=<id>          */
  const char *sigla;   /* Actuation Behavior Contract sigla      */
  int class_index;     /* index in the frozen class order        */
  float threshold;     /* model/thresholds.json commands.<id>    */
  float margin;        /* model/thresholds.json commands.<id>    */
  const char *behavior; /* registry enum (T15 consumes)         */
  const char *channels; /* registry channel list (T15 consumes) */
} ULTRON_COMMANDS[ULTRON_NUM_COMMANDS] = {
    {
        "la",
        "LA",
        3,
        0.8f,
        0.1f,
        "steady",
        "la,lb,lm",
    },
    {
        "lb",
        "LB",
        2,
        0.8f,
        0.1f,
        "steady",
        "lb",
    },
};

/* Full registry (model/registry.json) — the output controller's command table
 * (spec 003 T15). The engine resolves every CMD id through this table, including
 * commands the current model does not recognise yet (spec 006 rollout), so the
 * behaviour mapping is never duplicated in C. `channels` is a CSV of registry
 * channel ids; `behavior` is the registry enum:
 *   steady | blink_45s | blink_until_cancel | cancel | motor | pulse_2s | pulse_05s
 */
#define ULTRON_REGISTRY_SIZE 11
typedef struct {
  const char *id;       /* registry id — matches EVT CMD id=<id>           */
  const char *sigla;    /* Actuation Behavior Contract sigla               */
  const char *behavior; /* registry behavior enum (see above)              */
  const char *channels; /* registry channel ids, CSV (empty = drives none) */
} ultron_registry_entry;
static const ultron_registry_entry ULTRON_REGISTRY[ULTRON_REGISTRY_SIZE] = {
    {
        "lm",
        "LM",
        "steady",
        "lm",
    },
    {
        "lb",
        "LB",
        "steady",
        "lb",
    },
    {
        "la",
        "LA",
        "steady",
        "la,lb,lm",
    },
    {
        "gd",
        "GD",
        "blink_45s",
        "gd",
    },
    {
        "gi",
        "GI",
        "blink_45s",
        "gi",
    },
    {
        "p",
        "P",
        "blink_until_cancel",
        "gd,gi",
    },
    {
        "cp",
        "CP",
        "cancel",
        "",
    },
    {
        "lpb",
        "LPB",
        "motor",
        "lpb",
    },
    {
        "lpa",
        "LPA",
        "motor",
        "lpa",
    },
    {
        "a",
        "A",
        "pulse_2s",
        "a",
    },
    {
        "cc",
        "CC",
        "pulse_05s",
        "cc",
    },
};

#endif /* ULTRON_CONFIG_H */
