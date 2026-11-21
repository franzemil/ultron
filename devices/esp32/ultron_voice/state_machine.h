/*
 * state_machine — the ultron three-state interaction machine (spec 003-can-act).
 *
 * Pure C99, no Arduino dependency: the same translation unit is compiled into
 * the XIAO firmware and into the host test harness (tests/test_esp32_state_machine.py),
 * so the transition table proven on the host is literally the firmware's table.
 *
 * States and transitions (design.md → "State machine"):
 *
 *   WAKE_LISTEN -- wake >= wake_threshold + margin --> CONFIRMING  (emit WAKE)
 *   WAKE_LISTEN -- below threshold ------------------> WAKE_LISTEN (silent)
 *   CONFIRMING  -- playback ends --------------------> LISTENING   (start 5 s timer)
 *   LISTENING   -- command gate met -----------------> WAKE_LISTEN (emit CMD)
 *   LISTENING   -- wake wins its window -------------> LISTENING   (refresh timer once, Q-005)
 *   LISTENING   -- 5 s elapsed ----------------------> WAKE_LISTEN (emit TIMEOUT)
 *
 * Requirements implemented here:
 *   FR-001 wake entry, FR-002 command window, FR-003 command acceptance
 *   (threshold + margin over every other class), FR-004 timeout, FR-005 no
 *   command events while wake-listening, FR-006 stay silent below threshold,
 *   FR-008 event emission, FR-019/FR-037 playback-overlap discard, Q-005
 *   one-time timer refresh.
 *
 * Event text formatting lives here too (ultron_sm_format_event) so the contract
 * lines the host test asserts are byte-identical to the ones the device prints.
 */

#ifndef ULTRON_STATE_MACHINE_H
#define ULTRON_STATE_MACHINE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Sized for the registry cap (13 classes) with slack; the trained subset is 4. */
#define ULTRON_MAX_CLASSES 24
#define ULTRON_MAX_COMMANDS 12

typedef struct {
  int class_index;      /* index in the frozen class order */
  const char *id;       /* registry id, e.g. "lb" (EVT CMD id=…) */
  float threshold;      /* model/thresholds.json → commands.<id>.threshold */
  float margin;         /* model/thresholds.json → commands.<id>.margin */
} ultron_cmd_gate;

typedef struct {
  int n_classes;
  const char *const *class_names; /* frozen class order (wake noise lb la …) */
  int wake_class;
  float wake_threshold;
  float wake_margin;
  int n_commands;
  const ultron_cmd_gate *commands;
  uint32_t window_ms; /* command window, 5 s (thresholds.json window_s) */
} ultron_sm_config;

typedef enum {
  ULTRON_EVT_WAKE = 0,
  ULTRON_EVT_CMD,
  ULTRON_EVT_TIMEOUT,
  ULTRON_EVT_ERROR
} ultron_event_type;

typedef struct {
  ultron_event_type type;
  int class_index;        /* CMD: winning class */
  const char *cmd_id;     /* CMD: registry id */
  float conf;             /* WAKE / CMD: winning score */
  uint32_t t_ms;          /* ms since boot (ignored for ERROR) */
  const char *error_code; /* ERROR: e.g. MIC_INIT_FAILED */
} ultron_event;

typedef void (*ultron_emit_fn)(const ultron_event *evt, void *user);

typedef enum {
  ULTRON_SM_WAKE_LISTEN = 0,
  ULTRON_SM_CONFIRMING,
  ULTRON_SM_LISTENING
} ultron_state;

typedef struct {
  const ultron_sm_config *cfg;
  ultron_state state;
  uint32_t window_start_ms;   /* command window timer origin (playback end) */
  bool window_refreshed;      /* Q-005: at most one refresh per window */
  bool playback_active;       /* a feedback clip is (or was) playing */
  uint32_t playback_start_ms; /* valid while playback_active */
  uint32_t playback_end_ms;   /* valid while playback_active */
  ultron_emit_fn emit;
  void *emit_user;
  uint32_t windows_seen;    /* DBG counter: classified windows processed */
  uint32_t windows_discarded; /* DBG counter: windows dropped by the FR-019 rule */
} ultron_sm;

void ultron_sm_init(ultron_sm *sm, const ultron_sm_config *cfg, ultron_emit_fn emit,
                    void *user);

/* Feedback hooks (T14/T15 use them; console-only T12 leaves playback at 0 ms).
 * FR-019/FR-037: a window whose captured frames overlap a playback interval is
 * excluded from decisions; inference and DBG logging continue. */
void ultron_sm_playback_start(ultron_sm *sm, uint32_t now_ms, uint32_t duration_ms);
void ultron_sm_playback_end(ultron_sm *sm, uint32_t now_ms);

/* Advance the machine with one classified window.
 *   scores          : n_classes probabilities (softmax output of the model)
 *   window_start_ms : capture time of the first frame in the window
 *   now_ms          : time the scores became available
 * Returns the state after the update. */
ultron_state ultron_sm_update(ultron_sm *sm, const float *scores, uint32_t window_start_ms,
                              uint32_t now_ms);

/* Emit helper for non-state-machine errors (boot failures) — same formatting. */
void ultron_sm_emit_error(ultron_sm *sm, const char *code);

/* Format one event as its Target Event Contract serial line (design.md →
 * "Event contract"). Returns the number of characters written (excluding the
 * NUL), or 0 if the buffer is too small. Lines never carry a trailing newline. */
size_t ultron_sm_format_event(const ultron_event *evt, char *buf, size_t n);

/* Format the NFR-008 prediction line body (no trailing newline):
 *   "DBG pred=<class> conf=<f> state=<state> t_ms=<ms>" (+ optional extras) */
size_t ultron_sm_format_pred(const ultron_sm *sm, const float *scores, const char *state_name,
                             uint32_t t_ms, char *buf, size_t n);

const char *ultron_sm_state_name(ultron_state st);

#ifdef __cplusplus
}
#endif

#endif /* ULTRON_STATE_MACHINE_H */
