/*
 * sm_host_test — host driver for the firmware state machine (spec 003 T12).
 *
 * Compiles the *firmware* translation unit (devices/esp32/ultron_voice/state_machine.c)
 * together with the *generated* config (config.h ← model/thresholds.json +
 * model/registry.json) and drives it with scripted windows. Every contract line
 * printed here comes from the same ultron_sm_format_event() the device prints, so
 * the strings asserted in tests/test_esp32_state_machine.py are the device's
 * strings, not a re-implementation.
 *
 * Scenarios (requirement in brackets):
 *   1. below-threshold audio stays silent                        [FR-006]
 *   2. wake acceptance and its exact WAKE line                   [FR-001, FR-008]
 *   3. commands are ignored outside the command window            [FR-005]
 *   4. command acceptance, margin and threshold gates             [FR-003, FR-008]
 *   5. 5 s timeout                                               [FR-004]
 *   6. one-time timer refresh on a wake inside the window         [Q-005]
 *   7. playback-overlap windows never decide                      [FR-019, FR-037]
 *   8. the command window starts at playback end                  [FR-019, NFR-010]
 *
 * Build/run (this is what the pytest wrapper does):
 *   cc -std=c99 -Wall -Wextra -I devices/esp32/ultron_voice \
 *      tests/esp32/sm_host_test.c devices/esp32/ultron_voice/state_machine.c \
 *      -o /tmp/sm_host_test && /tmp/sm_host_test
 */

#include <stdio.h>
#include <string.h>

#include "config_sm.h"

static int g_failures = 0;
static char g_lines[16][96];
static int g_n_lines = 0;
static ultron_sm *g_sm_for_sink = NULL;
static uint32_t g_wake_clip_ms = 0; /* >0: arm a clip on every WAKE, like T14 does */

static void sink(const ultron_event *evt, void *user) {
  (void)user;
  /* Feedback layer contract (state_machine.h): arm the clip synchronously on the
   * WAKE edge, before ultron_sm_update() returns. This is exactly T14's wiring. */
  if (evt->type == ULTRON_EVT_WAKE && g_wake_clip_ms > 0 && g_sm_for_sink != NULL) {
    ultron_sm_playback_start(g_sm_for_sink, evt->t_ms, g_wake_clip_ms);
  }
  char buf[96];
  if (ultron_sm_format_event(evt, buf, sizeof(buf)) == 0) return;
  printf("%s\n", buf);
  if (g_n_lines < (int)(sizeof(g_lines) / sizeof(g_lines[0]))) {
    snprintf(g_lines[g_n_lines++], sizeof(g_lines[0]), "%s", buf);
  }
}

static void reset_log(void) { g_n_lines = 0; }

static const char *line(int i) { return (i >= 0 && i < g_n_lines) ? g_lines[i] : ""; }

#define CHECK(cond, msg)                                        \
  do {                                                          \
    if (!(cond)) {                                              \
      printf("FAIL %s (%s:%d)\n", (msg), __FILE__, __LINE__);   \
      g_failures++;                                             \
    }                                                           \
  } while (0)

#define CHECK_LINE(index, expected)                                                 \
  do {                                                                              \
    if (strcmp(line(index), (expected)) != 0) {                                      \
      printf("FAIL line %d: expected \"%s\", got \"%s\"\n", (index), (expected),      \
             line(index));                                                           \
      g_failures++;                                                                  \
    }                                                                                \
  } while (0)

/* ── score helpers ───────────────────────────────────────────────────────── */

/* Quiet window: every command class far below its gate, wake below its gate. */
static void quiet_scores(float *scores, int n) {
  for (int i = 0; i < n; i++) scores[i] = 0.01f;
  scores[ULTRON_NOISE_CLASS] = 0.90f;
}

/* A window dominated by one command class with a flat low background. */
static void command_scores(float *scores, int n, int class_index, float conf) {
  for (int i = 0; i < n; i++) scores[i] = 0.01f;
  if (class_index >= 0) scores[class_index] = conf;
}

static int class_of(const char *id) {
  for (int i = 0; i < ULTRON_NUM_COMMANDS; i++) {
    if (strcmp(ULTRON_COMMANDS[i].id, id) == 0) return ULTRON_COMMANDS[i].class_index;
  }
  return -1;
}

/* ── fixture ─────────────────────────────────────────────────────────────── */

typedef struct {
  ultron_sm sm;
  ultron_sm_config cfg;
  ultron_cmd_gate gates[ULTRON_NUM_COMMANDS];
  float scores[ULTRON_NUM_CLASSES];
} fixture;

static void fixture_reset(fixture *f) {
  memset(f, 0, sizeof(*f));
  ultron_config_sm(&f->cfg, f->gates);
  ultron_sm_init(&f->sm, &f->cfg, sink, NULL);
  reset_log();
}

/* ── scenarios ───────────────────────────────────────────────────────────── */

/* 1. FR-006 — below-threshold audio stays silent, in any state. */
static void scenario_silence(fixture *f) {
  fixture_reset(f);
  for (uint32_t t = 500; t <= 5000; t += 500) {
    quiet_scores(f->scores, ULTRON_NUM_CLASSES);
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  CHECK(g_n_lines == 0, "below-threshold audio must emit nothing");
  CHECK(f->sm.state == ULTRON_SM_WAKE_LISTEN, "state must stay WAKE_LISTEN");
  printf("OK silence\n");
}

/* 2. FR-001 / FR-008 — wake acceptance and the exact contract line. */
static void scenario_wake(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 10345, 12345);
  CHECK(g_n_lines == 1, "one WAKE line expected");
  CHECK_LINE(0, "EVT WAKE conf=0.94 t_ms=12345");
  CHECK(f->sm.state == ULTRON_SM_LISTENING,
        "console-only build (no clip) must be LISTENING right after the wake");
  CHECK(f->sm.window_start_ms == 12345, "command window starts at acceptance");
  printf("OK wake\n");
}

/* 2b. below the wake gate (threshold + margin) nothing happens. */
static void scenario_wake_below_gate(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.85f; /* 0.80 + 0.10 gate → below */
  ultron_sm_update(&f->sm, f->scores, 500, 2500);
  CHECK(g_n_lines == 0, "0.85 wake must stay below the 0.90 gate");
  CHECK(f->sm.state == ULTRON_SM_WAKE_LISTEN, "state must stay WAKE_LISTEN");
  printf("OK wake_below_gate\n");
}

/* 3. FR-005 — a command phrase with no wake in front of it does nothing. */
static void scenario_command_without_wake(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[class_of("lb")] = 0.95f;
  for (uint32_t t = 500; t <= 3000; t += 500) {
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  CHECK(g_n_lines == 0, "a bare command must never emit CMD");
  printf("OK command_without_wake\n");
}

/* 4. FR-003 / FR-008 — command acceptance inside the window. */
static void scenario_command(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 500, 2500); /* WAKE */
  reset_log();

  command_scores(f->scores, ULTRON_NUM_CLASSES, class_of("lb"), 0.91f);
  ultron_sm_update(&f->sm, f->scores, 3000, 5000);
  CHECK(g_n_lines == 1, "one CMD line expected");
  CHECK_LINE(0, "EVT CMD id=lb conf=0.91 t_ms=5000");
  CHECK(f->sm.state == ULTRON_SM_WAKE_LISTEN, "CMD returns to wake-listening");
  printf("OK command\n");
}

/* 4b. threshold gate: 0.88 < 0.80 + 0.10. */
static void scenario_command_below_threshold(fixture *f) {
  fixture_reset(f);
  int lb = class_of("lb");
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 500, 2500);
  reset_log();

  command_scores(f->scores, ULTRON_NUM_CLASSES, lb, 0.88f);
  ultron_sm_update(&f->sm, f->scores, 3000, 5000);
  CHECK(g_n_lines == 0, "0.88 must not pass the 0.90 threshold gate");
  CHECK(f->sm.state == ULTRON_SM_LISTENING, "must stay in the command window");
  printf("OK command_below_threshold\n");
}

/* 4c. margin gate: 0.92 wins, but only 0.04 ahead of the runner-up (< 0.10). */
static void scenario_command_below_margin(fixture *f) {
  fixture_reset(f);
  int lb = class_of("lb");
  int other = (lb == 0) ? 1 : 0; /* any class that is not lb */
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 500, 2500);
  reset_log();

  command_scores(f->scores, ULTRON_NUM_CLASSES, lb, 0.92f);
  f->scores[other] = 0.88f;
  ultron_sm_update(&f->sm, f->scores, 3000, 5000);
  CHECK(g_n_lines == 0, "0.92 vs 0.88 is below the 0.10 margin");
  printf("OK command_below_margin\n");
}

/* 5. FR-004 — 5 s without a command emits TIMEOUT and returns to wake. */
static void scenario_timeout(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 500, 2500); /* WAKE at 2500 */
  reset_log();

  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  for (uint32_t t = 3000; t <= 7500; t += 500) {
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  CHECK(g_n_lines == 1, "exactly one TIMEOUT line expected");
  CHECK_LINE(0, "EVT TIMEOUT t_ms=7500");
  CHECK(f->sm.state == ULTRON_SM_WAKE_LISTEN, "TIMEOUT returns to wake-listening");
  printf("OK timeout\n");
}

/* 6. Q-005 — a wake inside the window refreshes the timer exactly once. */
static void scenario_refresh_once(fixture *f) {
  fixture_reset(f);
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 500, 2500); /* WAKE; window starts at 2500 */
  reset_log();

  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  for (uint32_t t = 3000; t <= 4000; t += 500) {
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  f->scores[ULTRON_WAKE_CLASS] = 0.93f;
  ultron_sm_update(&f->sm, f->scores, 4500, 4500); /* refresh → window starts at 4500 */
  CHECK(g_n_lines == 0, "a refresh inside the window emits nothing");

  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  /* second wake at 9000 must NOT refresh again (Q-005: max once) */
  f->scores[ULTRON_WAKE_CLASS] = 0.93f;
  ultron_sm_update(&f->sm, f->scores, 7000, 9000);
  CHECK(g_n_lines == 0, "second refresh must be ignored");

  /* the timer still runs from 4500 → TIMEOUT at 9500, not 14000 */
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  for (uint32_t t = 9500; t <= 10000; t += 500) {
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  CHECK(g_n_lines == 1, "exactly one TIMEOUT expected after the single refresh");
  CHECK_LINE(0, "EVT TIMEOUT t_ms=9500");
  printf("OK refresh_once\n");
}

/* 7. FR-019 / FR-037 — a window that heard the device's own clip never decides. */
static void scenario_playback_overlap(fixture *f) {
  fixture_reset(f);
  /* Arm a 500 ms clip that plays 1000..1500 ms after boot (T14's "Escuchando"). */
  ultron_sm_playback_start(&f->sm, 1000, 500);

  /* Window captured 800..1500 — fully overlapping the clip, wake score 0.95. */
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.95f;
  ultron_sm_update(&f->sm, f->scores, 800, 1500);
  CHECK(g_n_lines == 0, "a window overlapping playback must not emit WAKE");
  CHECK(f->sm.windows_discarded == 1, "the discarded window must be counted");

  /* A later window (1600..2000) is clean and may decide. */
  ultron_sm_update(&f->sm, f->scores, 1600, 2000);
  CHECK(g_n_lines == 1, "a clean window after playback decides normally");
  CHECK_LINE(0, "EVT WAKE conf=0.95 t_ms=2000");
  printf("OK playback_overlap\n");
}

/* 8. FR-019 / NFR-010 — with a clip in CONFIRMING, the 5 s window starts at
 * playback end and the overlapping windows decide nothing. */
static void scenario_window_starts_at_playback_end(fixture *f) {
  /* T14's sequence: the WAKE sink arms a 500 ms "Escuchando" clip at 5000..5500. */
  fixture_reset(f);
  g_sm_for_sink = &f->sm;
  g_wake_clip_ms = 500;
  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  f->scores[ULTRON_WAKE_CLASS] = 0.94f;
  ultron_sm_update(&f->sm, f->scores, 3000, 5000);
  g_wake_clip_ms = 0;
  g_sm_for_sink = NULL;
  CHECK(f->sm.state == ULTRON_SM_CONFIRMING, "an armed clip must hold CONFIRMING");
  reset_log();

  quiet_scores(f->scores, ULTRON_NUM_CLASSES);
  ultron_sm_update(&f->sm, f->scores, 3500, 5500); /* clip ends here → LISTENING */
  CHECK(f->sm.state == ULTRON_SM_LISTENING, "clip end switches to LISTENING");
  CHECK(f->sm.windows_discarded == 1, "the window spanning the clip is discarded");
  ultron_sm_update(&f->sm, f->scores, 4000, 6000); /* still overlapping → discarded */
  CHECK(f->sm.windows_discarded == 2, "later overlapping windows stay discarded");
  CHECK(f->sm.window_start_ms == 5500, "command window starts at playback end");

  /* 5 s from 5500 → TIMEOUT at 10500 (not 10000 from the acceptance edge). */
  for (uint32_t t = 6500; t <= 11000; t += 500) {
    ultron_sm_update(&f->sm, f->scores, t - 2000, t);
  }
  CHECK(g_n_lines == 1, "exactly one TIMEOUT expected");
  CHECK_LINE(0, "EVT TIMEOUT t_ms=10500");
  printf("OK window_starts_at_playback_end\n");
}

/* 9. fail-fast error line (boot failures use the same formatter). */
static void scenario_error_line(fixture *f) {
  fixture_reset(f);
  ultron_sm_emit_error(&f->sm, "MIC_INIT_FAILED");
  CHECK(g_n_lines == 1, "one ERROR line expected");
  CHECK_LINE(0, "EVT ERROR code=MIC_INIT_FAILED");
  printf("OK error\n");
}

int main(void) {
  fixture f;
  printf("--- generated config ---\n");
  printf("classes=%d wake=%d thr=%.2f+%.2f commands=%d window=%lums\n", ULTRON_NUM_CLASSES,
         ULTRON_WAKE_CLASS, (double)ULTRON_WAKE_THRESHOLD, (double)ULTRON_WAKE_MARGIN,
         ULTRON_NUM_COMMANDS, (unsigned long)ULTRON_WINDOW_MS);
  for (int i = 0; i < ULTRON_NUM_COMMANDS; i++) {
    printf("gate id=%s class=%d thr=%.2f+%.2f\n", ULTRON_COMMANDS[i].id,
           ULTRON_COMMANDS[i].class_index, (double)ULTRON_COMMANDS[i].threshold,
           (double)ULTRON_COMMANDS[i].margin);
  }

  printf("--- scenarios ---\n");
  scenario_silence(&f);
  scenario_wake(&f);
  scenario_wake_below_gate(&f);
  scenario_command_without_wake(&f);
  scenario_command(&f);
  scenario_command_below_threshold(&f);
  scenario_command_below_margin(&f);
  scenario_timeout(&f);
  scenario_refresh_once(&f);
  scenario_playback_overlap(&f);
  scenario_window_starts_at_playback_end(&f);
  scenario_error_line(&f);

  if (g_failures == 0) {
    printf("ALL OK\n");
    return 0;
  }
  printf("%d FAILURE(S)\n", g_failures);
  return 1;
}
