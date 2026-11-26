/*
 * UltronVoice — Arduino library: wake word + command recognition for the
 * Seeed XIAO ESP32S3 Sense (spec 003-can-act).
 *
 * It wraps everything below the application:
 *
 *   PDM mic (I2S0) ─► 2 s ring / 500 ms hop ─► shared C MFCC (100 × 13)
 *      ─► int8 [1,100,13] ─► TFLite-Micro (AllOpsResolver, static arena)
 *      ─► WAKE_LISTEN / CONFIRMING / LISTENING ─► Event { Wake, Command, Timeout, Error }
 *
 * The sketch only writes the *application* logic — what to do when a command is
 * recognised (open a relay, turn a light on, …). Everything timing-critical
 * (capture, features, inference, thresholds, the 5 s window, the Q-005 refresh,
 * the FR-019/FR-037 playback-overlap discard) lives in here, so every target and
 * every sketch behaves identically (AGENTS rule 5).
 *
 * Thresholds, the frozen class order and the trained command table are NOT
 * compiled into the C++ sources: `ultron_config.h` is generated from
 * `model/thresholds.json` + `model/registry.json` (scripts/gen_esp32_config.py).
 * Model bytes come from the generated `model_data.h`.
 *
 * Console (NFR-008): one `DBG pred=` line per classified window and the Target
 * Event Contract lines (`EVT WAKE conf=… t_ms=…`, `EVT CMD id=… conf=… t_ms=…`,
 * `EVT TIMEOUT t_ms=…`, `EVT ERROR code=…`) are printed by the library on the
 * Stream you give it (default `Serial`).
 *
 * Minimal use:
 *
 *   #include <UltronVoice.h>
 *   UltronVoice voice;
 *
 *   void setup() { voice.begin(); }               // prints the boot banner
 *   void loop() {
 *     UltronVoice::Event ev;
 *     if (voice.poll(ev) && ev.type == UltronVoice::Type::Command) {
 *       if (strcmp(ev.id, "lb") == 0) { /* open the relay *\/ }
 *     }
 *   }
 */

#ifndef ULTRON_VOICE_H
#define ULTRON_VOICE_H

#include <Arduino.h>

#include "ultron_config.h"    /* GENERATED: thresholds + registry subset */
#include "ultron_config_sm.h" /* generated config → ultron_sm_config */
#include "ultron_features.h"
#include "ultron_infer.h"
#include "ultron_state_machine.h"
#include "pdm_capture.h"

class UltronVoice {
 public:
  enum class Type : uint8_t {
    Wake = 0,   /* "hey ultron" accepted  (FR-001) */
    Command,    /* command accepted       (FR-003) */
    Timeout,    /* 5 s window elapsed     (FR-004) */
    Error,      /* boot/runtime fault     (fail-fast) */
  };

  struct Event {
    Type type = Type::Error;
    const char *id = nullptr;      /* Command: registry id ("lb", "la", …) */
    int classIndex = -1;           /* frozen class index, -1 when N/A */
    float conf = 0.0f;             /* winning score for Wake/Command */
    uint32_t tMs = 0;              /* ms since boot */
    const char *errorCode = nullptr; /* Error: e.g. MIC_INIT_FAILED */
  };

  struct Options {
    /* Feedback clips (T14). The library arms the playback interval on the event
     * edge so the 5 s window starts when playback ends (FR-019/NFR-010); the
     * *application* plays the audio. 0 = no clip (console-only build). */
    uint32_t wakeClipMs = 0;
    uint32_t commandClipMs = 0;
    /* Scripted scores instead of the microphone: proves the state machine and the
     * event contract on hardware, never recognition (see ultron_selftest.h). */
    bool selftest = false;
    /* Console for EVT/DBG lines; nullptr = the global `Serial`. */
    Stream *console = nullptr;
  };

  struct Stats {
    size_t modelBytes = 0;
    size_t arenaBytes = 0;
    size_t arenaUsed = 0;
    size_t ringBytes = 0;
    size_t mfccBytes = 0;
    size_t inferenceRam = 0;
    bool placeholder = false;
    int classes = 0;
    int commands = 0;
  };

  UltronVoice() = default;

  /* Bring up the interpreter, the MFCC tables and the microphone, then print the
   * boot banner. Returns false (after emitting EVT ERROR) if anything fails —
   * a deaf or mute device must never pretend to listen. */
  bool begin();
  bool begin(const Options &options);

  /* Advance the pipeline by one capture chunk. Returns true when an Event was
   * produced (at most one per 500 ms window). Non-blocking in the sense that it
   * returns promptly; it performs one 100 ms microphone read. */
  bool poll(Event &out);

  const Stats &stats() const { return stats_; }
  bool ready() const { return ready_; }
  const char *stateName() const { return ultron_sm_state_name(sm_.state); }
  uint32_t windowsSeen() const { return sm_.windows_seen; }
  uint32_t windowsDiscarded() const { return sm_.windows_discarded; }
  /* Last classified window: latency of the whole NFR-001 budget and of Invoke(). */
  uint32_t lastWindowMs() const { return last_window_ms_; }
  uint32_t lastInferenceMs() const { return last_infer_ms_; }

  Stream &console() { return *console_; }

 private:
  static void onEvent(const ultron_event *evt, void *user);
  void emit(const ultron_event *evt);
  void printBootBanner();
  void printMemory();
  void classify();
  void fail(const char *code, const char *detail = nullptr);

  Options options_;
  Stream *console_ = &Serial;
  ultron::PdmCapture mic_;
  ultron::FeatureExtractor features_;
  ultron::ModelStats model_;
  ultron_sm sm_{};
  ultron_sm_config sm_config_{};
  ultron_cmd_gate gates_[ULTRON_NUM_COMMANDS]{};

  int16_t chunk_[1600]{};                       /* 100 ms of audio (static)  */
  int8_t input_[ultron::FeatureExtractor::kFeatureCount]{}; /* 1300 int8 */
  float scores_[ULTRON_NUM_CLASSES]{};

  Event pending_{};
  bool has_pending_ = false;
  bool ready_ = false;
  bool halted_ = false;
  uint8_t mic_failures_ = 0;
  uint32_t last_window_ms_ = 0;
  uint32_t last_infer_ms_ = 0;
  uint32_t scripted_window_ = 0;
  float scripted_scores_[ULTRON_NUM_CLASSES]{};
  Stats stats_{};
};

#endif /* ULTRON_VOICE_H */
