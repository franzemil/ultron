/*
 * state_machine — implementation (spec 003-can-act, design.md → "State machine").
 *
 * Pure C99. No Arduino, no allocation, no I/O: the caller owns the clock, the
 * scores and the event sink. See state_machine.h for the transition table and
 * requirement mapping.
 */

#include "state_machine.h"

#include <stdio.h>
#include <string.h>

/* ── helpers ─────────────────────────────────────────────────────────────── */

static int argmax_scores(const float *scores, int n) {
  int best = 0;
  for (int i = 1; i < n; i++) {
    if (scores[i] > scores[best]) best = i;
  }
  return best;
}

static const ultron_cmd_gate *gate_for_class(const ultron_sm_config *cfg, int class_index) {
  for (int i = 0; i < cfg->n_commands; i++) {
    if (cfg->commands[i].class_index == class_index) return &cfg->commands[i];
  }
  return NULL;
}

/* FR-019 / FR-037: does [window_start_ms, now_ms] intersect the playback
 * interval [playback_start, playback_end)? If yes the window's frames heard the
 * device's own clip and must not decide anything.
 *
 * The interval outlives the clip on purpose: a 2 s window captured while the
 * clip played keeps overlapping it for up to 2 s after playback ends, and it
 * must stay discarded for as long as it overlaps. */
static bool window_overlaps_playback(const ultron_sm *sm, uint32_t window_start_ms,
                                     uint32_t now_ms) {
  if (!sm->playback_interval_valid) return false;
  return window_start_ms < sm->playback_end_ms && now_ms > sm->playback_start_ms;
}

static void emit(ultron_sm *sm, ultron_event *evt) {
  if (sm->emit != NULL) sm->emit(evt, sm->emit_user);
}

static void emit_timeout(ultron_sm *sm, uint32_t now_ms) {
  ultron_event evt;
  memset(&evt, 0, sizeof(evt));
  evt.type = ULTRON_EVT_TIMEOUT;
  evt.t_ms = now_ms;
  emit(sm, &evt);
  sm->state = ULTRON_SM_WAKE_LISTEN;
}

/* Leave CONFIRMING once the feedback clip (if any) has finished; the 5 s
 * command window starts when playback ends (FR-019 / NFR-010). */
static void finish_confirming(ultron_sm *sm, uint32_t now_ms) {
  const uint32_t timer_origin = sm->playback_interval_valid ? sm->playback_end_ms : now_ms;
  sm->state = ULTRON_SM_LISTENING;
  sm->window_start_ms = timer_origin;
  sm->window_refreshed = false;
  sm->playback_active = false; /* the interval survives in start/end for FR-019 */
}

/* ── public API ──────────────────────────────────────────────────────────── */

void ultron_sm_init(ultron_sm *sm, const ultron_sm_config *cfg, ultron_emit_fn emit_fn,
                    void *user) {
  memset(sm, 0, sizeof(*sm));
  sm->cfg = cfg;
  sm->emit = emit_fn;
  sm->emit_user = user;
  sm->state = ULTRON_SM_WAKE_LISTEN;
}

void ultron_sm_playback_start(ultron_sm *sm, uint32_t now_ms, uint32_t duration_ms) {
  sm->playback_active = true;
  sm->playback_interval_valid = true;
  sm->playback_start_ms = now_ms;
  sm->playback_end_ms = now_ms + duration_ms;
}

void ultron_sm_playback_end(ultron_sm *sm, uint32_t now_ms) {
  if (!sm->playback_active) return;
  sm->playback_end_ms = now_ms;
  if (now_ms >= sm->playback_start_ms) sm->playback_active = false;
}

void ultron_sm_emit_error(ultron_sm *sm, const char *code) {
  ultron_event evt;
  memset(&evt, 0, sizeof(evt));
  evt.type = ULTRON_EVT_ERROR;
  evt.error_code = code;
  emit(sm, &evt);
}

const char *ultron_sm_state_name(ultron_state st) {
  switch (st) {
    case ULTRON_SM_WAKE_LISTEN: return "WAKE_LISTEN";
    case ULTRON_SM_CONFIRMING: return "CONFIRMING";
    case ULTRON_SM_LISTENING: return "LISTENING";
  }
  return "?";
}

ultron_state ultron_sm_update(ultron_sm *sm, const float *scores, uint32_t window_start_ms,
                              uint32_t now_ms) {
  const ultron_sm_config *cfg = sm->cfg;
  if (cfg == NULL || scores == NULL) return sm->state;

  sm->windows_seen++;

  /* Did this window's captured frames hear the device's own clip? Evaluated
   * against the playback interval as it stood while the frames were captured. */
  const bool overlaps = window_overlaps_playback(sm, window_start_ms, now_ms);

  if (sm->state == ULTRON_SM_CONFIRMING) {
    /* Still playing: no decision, no timeout, and inference/DBG keeps running
     * (design.md → "State machine": CONFIRMING windows decide nothing). */
    if (sm->playback_active && now_ms < sm->playback_end_ms) return sm->state;
    finish_confirming(sm, now_ms); /* 5 s window starts at playback end */
    if (overlaps) {
      sm->windows_discarded++; /* first post-clip window still overlaps → discard */
      return sm->state;
    }
  } else if (overlaps) {
    sm->windows_discarded++;
    /* A discarded window never decides, but the 5 s budget is wall-clock:
     * the timeout may still fire while the clip tail is in flight (FR-004). */
    if (sm->state == ULTRON_SM_LISTENING &&
        (uint32_t)(now_ms - sm->window_start_ms) >= cfg->window_ms) {
      emit_timeout(sm, now_ms);
    }
    return sm->state;
  }

  const int winner = argmax_scores(scores, cfg->n_classes);

  if (sm->state == ULTRON_SM_WAKE_LISTEN) {
    /* FR-001: wake ≥ wake_threshold + margin (design table row 1). Below the
     * gate nothing is emitted — the device stays silent (FR-006). */
    if (winner == cfg->wake_class &&
        scores[cfg->wake_class] >= cfg->wake_threshold + cfg->wake_margin) {
      ultron_event evt;
      memset(&evt, 0, sizeof(evt));
      evt.type = ULTRON_EVT_WAKE;
      evt.class_index = cfg->wake_class;
      evt.conf = scores[cfg->wake_class];
      evt.t_ms = now_ms;
      emit(sm, &evt);

      sm->state = ULTRON_SM_CONFIRMING;
      /* The WAKE sink (feedback layer, T14) arms the clip synchronously through
       * ultron_sm_playback_start(), so CONFIRMING lasts exactly as long as the
       * clip. Console-only T12 arms nothing → CONFIRMING lasts 0 ms and the 5 s
       * window starts on this same tick (no listening time invented). */
      if (!(sm->playback_active && now_ms < sm->playback_end_ms)) {
        finish_confirming(sm, now_ms);
      }
    }
    return sm->state;
  }

  /* ── LISTENING ─────────────────────────────────────────────────────────── */
  if (winner == cfg->wake_class) {
    /* Q-005: the wake winning its window refreshes the 5 s timer — once. */
    if (!sm->window_refreshed) {
      sm->window_start_ms = now_ms;
      sm->window_refreshed = true;
    }
  } else {
    const ultron_cmd_gate *gate = gate_for_class(cfg, winner);
    if (gate != NULL) {
      /* FR-003: threshold + margin, and margin over every other class.
       * (winner is the argmax, so the second clause is the strict-margin one.) */
      float runner_up = 0.0f;
      for (int i = 0; i < cfg->n_classes; i++) {
        if (i != winner && scores[i] > runner_up) runner_up = scores[i];
      }
      if (scores[winner] >= gate->threshold + gate->margin &&
          (scores[winner] - runner_up) >= gate->margin) {
        ultron_event evt;
        memset(&evt, 0, sizeof(evt));
        evt.type = ULTRON_EVT_CMD;
        evt.class_index = winner;
        evt.cmd_id = gate->id;
        evt.conf = scores[winner];
        evt.t_ms = now_ms;
        emit(sm, &evt);
        sm->state = ULTRON_SM_WAKE_LISTEN;
        return sm->state;
      }
    }
  }

  /* FR-004: window elapsed → TIMEOUT, back to wake-listening. */
  if ((uint32_t)(now_ms - sm->window_start_ms) >= cfg->window_ms) {
    emit_timeout(sm, now_ms);
  }

  return sm->state;
}

/* ── contract formatting ─────────────────────────────────────────────────── */

size_t ultron_sm_format_event(const ultron_event *evt, char *buf, size_t n) {
  if (evt == NULL || buf == NULL || n == 0) return 0;
  int written = -1;
  switch (evt->type) {
    case ULTRON_EVT_WAKE:
      written = snprintf(buf, n, "EVT WAKE conf=%.2f t_ms=%lu", (double)evt->conf,
                         (unsigned long)evt->t_ms);
      break;
    case ULTRON_EVT_CMD:
      written = snprintf(buf, n, "EVT CMD id=%s conf=%.2f t_ms=%lu",
                         evt->cmd_id != NULL ? evt->cmd_id : "?", (double)evt->conf,
                         (unsigned long)evt->t_ms);
      break;
    case ULTRON_EVT_TIMEOUT:
      written = snprintf(buf, n, "EVT TIMEOUT t_ms=%lu", (unsigned long)evt->t_ms);
      break;
    case ULTRON_EVT_ERROR:
      written = snprintf(buf, n, "EVT ERROR code=%s",
                         evt->error_code != NULL ? evt->error_code : "UNKNOWN");
      break;
  }
  if (written < 0) {
    buf[0] = '\0';
    return 0;
  }
  if ((size_t)written >= n) {
    buf[n - 1] = '\0';
    return 0;
  }
  return (size_t)written;
}

size_t ultron_sm_format_pred(const ultron_sm *sm, const float *scores, const char *state_name,
                             uint32_t t_ms, char *buf, size_t n) {
  if (sm == NULL || sm->cfg == NULL || scores == NULL || buf == NULL || n == 0) return 0;
  const ultron_sm_config *cfg = sm->cfg;
  const int winner = argmax_scores(scores, cfg->n_classes);
  const char *name = (winner < cfg->n_classes && cfg->class_names != NULL)
                         ? cfg->class_names[winner]
                         : "?";
  int written = snprintf(buf, n, "DBG pred=%s conf=%.4f state=%s t_ms=%lu", name,
                         (double)scores[winner], state_name != NULL ? state_name : "?",
                         (unsigned long)t_ms);
  if (written < 0) {
    buf[0] = '\0';
    return 0;
  }
  if ((size_t)written >= n) {
    buf[n - 1] = '\0';
    return 0;
  }
  return (size_t)written;
}
