/*
 * selftest.h — scripted scores through the real state machine (T12 on-device proof).
 *
 * WHY THIS EXISTS: the trained spec-002 artifact (`model_int8.tflite`) is not
 * reproducible from the data on this machine (see the PR: no `build/features.npz`,
 * hold-out accuracy 0.25). T12 therefore runs a deterministic PLACEHOLDER model
 * — random weights, correct signature `[1, 100, 13]` int8 and correct 4-class
 * order — which proves the plumbing (interpreter, arena, latency, event lines)
 * but *cannot* recognise "hey ultron".
 *
 * So that the Target Event Contract can still be verified on real hardware, this
 * header injects a scripted score sequence into the *same* `ultron_sm_update()`
 * the microphone path uses. Every event line produced in this mode is real
 * formatting from the real state machine; only the scores are synthetic, and the
 * boot banner says so (`DBG selftest=1`). It is NOT recognition evidence and must
 * never be reported as US-001 AC1.
 *
 * Enable at build time only:
 *   arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 \
 *     --build-property "compiler.cpp.extra_flags=-DULTRON_SELFTEST=1" \
 *     devices/esp32/ultron_voice
 *
 * Script (one step per classified window; the loop runs one window per 500 ms):
 *   win 2   wake 0.94                     → EVT WAKE
 *   win 5   lb   0.91 (cmd, ≥0.8+0.1)     → EVT CMD id=lb
 *   win 8   wake 0.92                     → EVT WAKE (second cycle)
 *   win ~18 nothing above threshold       → EVT TIMEOUT (5 s after wake)
 *   win 20  wake 0.93                     → EVT WAKE (third cycle, T15)
 *   win 23  la   0.92                     → EVT CMD id=la (T15: LM + LB, LA unwired)
 * Windows 0..1, 3..4, 6..7, 9.. are quiet (noise-dominant, below every gate) so
 * the log also shows the machine staying silent (FR-006) and never emitting a
 * command outside the window (FR-005).
 *
 * The `la` cycle is what T15 (relays) verifies against: the trained model cannot
 * say "luz alta" yet, so the only honest way to drive `EVT CMD id=la` on the
 * bench is this scripted score line — the event formatting and the output
 * controller path are the real ones.
 */

#ifndef ULTRON_SELFTEST_H
#define ULTRON_SELFTEST_H

#include "ultron_config.h"

namespace selftest {

struct Step {
  uint32_t window; /* classified-window index (0-based, 500 ms per window) */
  int class_index; /* winning class, or -1 for a quiet window */
  float conf;      /* winning score */
};

/* Quiet window, then one accepted wake + command per cycle: lb (T12 contract
 * proof) and la (T15: drives two wired channels and one unwired one). */
static const Step kScript[] = {
    {2u, ULTRON_WAKE_CLASS, 0.94f},
    {5u, 2 /* lb */, 0.91f},
    {8u, ULTRON_WAKE_CLASS, 0.92f},
    {20u, ULTRON_WAKE_CLASS, 0.93f},
    {23u, 3 /* la */, 0.92f},
};
static const uint32_t kScriptLen = sizeof(kScript) / sizeof(kScript[0]);
static const uint32_t kQuietUntilWindow = 24u; /* past the la step (win 23) */

/* Fill `scores` (ULTRON_NUM_CLASSES entries) for window `index`.
 * Returns true while the script is still running. */
inline bool scores_for(uint32_t index, float *scores) {
  for (int i = 0; i < ULTRON_NUM_CLASSES; i++) scores[i] = 0.05f;
  /* Quiet baseline: the noise class dominates, well below every gate. */
  scores[ULTRON_NOISE_CLASS] = 0.80f;
  for (uint32_t i = 0; i < kScriptLen; i++) {
    if (kScript[i].window == index) {
      for (int c = 0; c < ULTRON_NUM_CLASSES; c++) scores[c] = 0.02f;
      scores[kScript[i].class_index] = kScript[i].conf;
      return true;
    }
  }
  return index < kQuietUntilWindow;
}

}  // namespace selftest

#endif /* ULTRON_SELFTEST_H */
