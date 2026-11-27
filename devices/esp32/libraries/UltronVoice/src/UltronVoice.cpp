/*
 * UltronVoice — implementation (spec 003-can-act T12 core, audio-feedback hooks for T14).
 *
 * This file is the old `ultron_voice.ino` body, unchanged in behaviour, lifted into
 * a library so a sketch only contains application logic. Console output format is
 * deliberately identical to the pre-library firmware (same `DBG …` / `EVT …`
 * lines) so existing logs, docs and the spec-004 bench keep working.
 */

#include "UltronVoice.h"

#include <stdio.h>
#include <string.h>

#include "ultron_selftest.h"

namespace {
constexpr size_t kChunkSamples = 1600;  /* 100 ms @ 16 kHz */
constexpr uint32_t kWindowMs = 2000;    /* analysis window */
constexpr uint8_t kMaxMicFailures = 5;
}  // namespace

/* ── event sink ──────────────────────────────────────────────────────────── */

void UltronVoice::onEvent(const ultron_event *evt, void *user) {
  static_cast<UltronVoice *>(user)->emit(evt);
}

void UltronVoice::emit(const ultron_event *evt) {
  char line[96];
  if (ultron_sm_format_event(evt, line, sizeof(line)) > 0) {
    console_->println(line);
  }
  /* Feedback timing hooks (T14). Arming here — inside the event sink, before
   * ultron_sm_update() returns — is what makes CONFIRMING last exactly as long as
   * the clip and the 5 s command window start at its end (FR-019 / NFR-010). For
   * a CMD the "comando" interval makes the overlapping windows non-deciding
   * (FR-037). The application plays the audio; the library owns the timing. */
  if (evt->type == ULTRON_EVT_WAKE && options_.wakeClipMs > 0) {
    ultron_sm_playback_start(&sm_, evt->t_ms, options_.wakeClipMs);
  } else if (evt->type == ULTRON_EVT_CMD && options_.commandClipMs > 0) {
    ultron_sm_playback_start(&sm_, evt->t_ms, options_.commandClipMs);
  }
  /* Only the newest event matters: the state machine emits at most one per
   * window, and the application polls immediately after each window. */
  pending_.type = static_cast<Type>(evt->type);
  pending_.id = evt->cmd_id;
  pending_.classIndex = evt->class_index;
  pending_.conf = evt->conf;
  pending_.tMs = evt->t_ms;
  pending_.errorCode = evt->error_code;
  has_pending_ = true;
}

void UltronVoice::fail(const char *code, const char *detail) {
  halted_ = true;
  ready_ = false;
  if (detail != nullptr) {
    char line[96];
    snprintf(line, sizeof(line), "DBG model_rc=%s", detail);
    console_->println(line);
  }
  ultron_sm_emit_error(&sm_, code);
  console_->println("DBG halted=1 — reboot to retry");
}

/* ── boot banner (NFR-008) ───────────────────────────────────────────────── */

void UltronVoice::printBootBanner() {
  char line[200];
  snprintf(line, sizeof(line),
           "DBG boot fw=ultron_voice lib=%s task=T12 console_only=1 classes=%d cmds=%d "
           "window_ms=%lu",
           ULTRON_LIBRARY_VERSION, ULTRON_NUM_CLASSES, ULTRON_NUM_COMMANDS,
           (unsigned long)ULTRON_WINDOW_MS);
  console_->println(line);
  console_->print("DBG config sha256=");
  console_->print(ULTRON_CONFIG_SHA256);
  console_->print(" order=");
  console_->println(ULTRON_CLASS_ORDER_SOURCE);

  snprintf(line, sizeof(line), "DBG thresholds wake=%.2f+%.2f", (double)ULTRON_WAKE_THRESHOLD,
           (double)ULTRON_WAKE_MARGIN);
  console_->println(line);
  for (int i = 0; i < ULTRON_NUM_COMMANDS; i++) {
    snprintf(line, sizeof(line), "DBG gate id=%s class=%d thr=%.2f+%.2f behavior=%s",
             ULTRON_COMMANDS[i].id, ULTRON_COMMANDS[i].class_index,
             (double)ULTRON_COMMANDS[i].threshold, (double)ULTRON_COMMANDS[i].margin,
             ULTRON_COMMANDS[i].behavior);
    console_->println(line);
  }
  if (options_.selftest) {
    console_->println("DBG selftest=1 scores=scripted evt_lines=real NOT_RECOGNITION");
  }
}

void UltronVoice::printMemory() {
  const size_t ring_bytes = ultron::FeatureExtractor::kRingSamples * sizeof(int16_t);
  const size_t io_bytes = sizeof(chunk_) + sizeof(input_) + sizeof(scores_);
  stats_.ringBytes = ring_bytes;
  stats_.mfccBytes = features_.mfccContextBytes();
  stats_.inferenceRam = stats_.arenaBytes + ring_bytes + io_bytes + stats_.mfccBytes;

  char line[256];
  snprintf(line, sizeof(line),
           "DBG mem model=%luB placeholder=%d arena=%luB arena_used=%luB ring=%luB io=%luB "
           "mfcc=%luB inference_ram=%luB heap_free=%luB",
           (unsigned long)stats_.modelBytes, stats_.placeholder ? 1 : 0,
           (unsigned long)stats_.arenaBytes, (unsigned long)stats_.arenaUsed,
           (unsigned long)ring_bytes, (unsigned long)io_bytes, (unsigned long)stats_.mfccBytes,
           (unsigned long)stats_.inferenceRam, (unsigned long)ESP.getFreeHeap());
  console_->println(line);

  snprintf(line, sizeof(line),
           "DBG model input=[1,%d,%d]%s int8 scale=%.8f zp=%d out_scale=%.8f out_zp=%d "
           "provenance=\"%s\"",
           model_.input_nwc ? model_.n_frames : model_.n_coeffs,
           model_.input_nwc ? model_.n_coeffs : model_.n_frames, model_.input_nwc ? " nwc" : " ncw",
           (double)model_.input_scale, model_.input_zero_point, (double)model_.output_scale,
           model_.output_zero_point, model_.provenance);
  console_->println(line);
}

/* ── setup ───────────────────────────────────────────────────────────────── */

bool UltronVoice::begin() { return begin(Options()); }

bool UltronVoice::begin(const Options &options) {
  options_ = options;
  console_ = options.console != nullptr ? options.console : &Serial;

  ultron_config_sm(&sm_config_, gates_);
  ultron_sm_init(&sm_, &sm_config_, &UltronVoice::onEvent, this);

  printBootBanner();

  /* 1. Model — parse the flatbuffer, map the tensors (NFR-002 arena). */
  const int model_rc = ultron::infer_begin(&model_);
  if (model_rc != 0) {
    char detail[8];
    snprintf(detail, sizeof(detail), "%d", model_rc);
    fail(model_rc == 2 ? "ARENA_TOO_SMALL" : "MODEL_INIT_FAILED", detail);
    return false;
  }
  if (model_.n_classes != ULTRON_NUM_CLASSES) {
    fail("MODEL_CLASS_MISMATCH");
    return false;
  }

  /* 2. Feature extractor — at boot: MFCC tables + the 64 KB ring buffer. */
  if (!features_.begin()) {
    fail("MFCC_INIT_FAILED");
    return false;
  }

  /* 3. PDM capture on I2S0 (the speaker side, T14, owns I2S1). */
  if (!mic_.begin()) {
    fail("MIC_INIT_FAILED");
    return false;
  }

  stats_.modelBytes = model_.model_bytes;
  stats_.arenaBytes = model_.arena_bytes;
  stats_.arenaUsed = model_.arena_used;
  stats_.placeholder = model_.placeholder;
  stats_.classes = model_.n_classes;
  stats_.commands = ULTRON_NUM_COMMANDS;
  printMemory();
  console_->println("DBG ready=1 listening=1");

  ready_ = true;
  return true;
}

/* ── inference + decision ────────────────────────────────────────────────── */

void UltronVoice::classify() {
  const uint32_t now = millis();
  const uint32_t win_t0 = now;
  if (!features_.buildWindowInt8(model_.input_scale, model_.input_zero_point, input_)) {
    return; /* ring not full yet (first 2 s after boot) */
  }

  const int rc = ultron::infer_run(input_, scores_, ULTRON_NUM_CLASSES);
  if (rc != 0) {
    char detail[8];
    snprintf(detail, sizeof(detail), "%d", rc);
    fail("INFER_FAILED", detail);
    return;
  }

  last_infer_ms_ = ultron::infer_lastLatencyUs() / 1000u;
  last_window_ms_ = millis() - win_t0;

  char line[160];
  size_t n = ultron_sm_format_pred(&sm_, scores_, ultron_sm_state_name(sm_.state), now, line,
                                   sizeof(line));
  if (n > 0 && n < sizeof(line)) {
    snprintf(line + n, sizeof(line) - n, " infer_ms=%lu win_ms=%lu",
             (unsigned long)last_infer_ms_, (unsigned long)last_window_ms_);
    console_->println(line);
  }

  const float *decision = scores_;
  if (options_.selftest) {
    const bool running = selftest::scores_for(scripted_window_, scripted_scores_);
    scripted_window_++;
    decision = scripted_scores_;
    ultron_sm_update(&sm_, decision, now - kWindowMs, now);
    if (!running && has_pending_ && pending_.type == Type::Timeout) {
      char done[64];
      snprintf(done, sizeof(done), "DBG selftest=done windows=%lu",
               (unsigned long)scripted_window_);
      console_->println(done);
      options_.selftest = false; /* one scripted run, then real scores (if any) */
    }
    return;
  }

  ultron_sm_update(&sm_, decision, now - kWindowMs, now);
}

/* ── loop ────────────────────────────────────────────────────────────────── */

bool UltronVoice::poll(Event &out) {
  if (has_pending_) {
    out = pending_;
    has_pending_ = false;
    return true;
  }
  if (!ready_ || halted_) return false;

  const size_t got = mic_.read(chunk_, kChunkSamples);
  if (got > 0) {
    mic_failures_ = 0;
    features_.push(chunk_, got);
    if (features_.hopReady()) classify();
  } else {
    /* A silent PDM stream is a hardware fault, not an empty room (Q-003). */
    if (++mic_failures_ >= kMaxMicFailures) {
      mic_failures_ = 0;
      ultron_sm_emit_error(&sm_, "MIC_READ_FAILED");
    }
  }

  if (has_pending_) {
    out = pending_;
    has_pending_ = false;
    return true;
  }
  return false;
}
