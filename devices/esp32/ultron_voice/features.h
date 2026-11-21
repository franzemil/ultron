/*
 * features — 2 s int16 ring buffer + 500 ms hop on the XIAO (spec 003 T12).
 *
 * Geometry (design.md → "Architecture", tasks.md T12):
 *   ring    : 2.0 s @ 16 kHz int16 mono = 32 000 samples = 64 KB (static)
 *   hop     : 500 ms = 8 000 samples → one classified window every 500 ms
 *   frames  : 100 x 20 ms, 13 MFCC coefficients (shared C library, spec 001)
 *
 * Memory rules: every buffer here is static (.bss). The inference path performs
 * no dynamic allocation; mfcc_init()'s table allocation happens once in begin()
 * and is reported as DBG mem=… at boot. Windows are read out of the ring with
 * modular indexing and one 320-sample frame scratch, so a full 2 s window is
 * never copied (that would cost a second 64 KB).
 */

#ifndef ULTRON_FEATURES_H
#define ULTRON_FEATURES_H

#include <stddef.h>
#include <stdint.h>

#include "features_core.h"

namespace ultron {

class FeatureExtractor {
 public:
  static constexpr size_t kRingSamples = ULTRON_WINDOW_SAMPLES; /* 32000 = 64 KB */
  static constexpr size_t kHopSamples = 8000;                   /* 500 ms        */
  static constexpr size_t kFrameSamples = ULTRON_FRAME_SAMPLES; /* 320 (20 ms)   */
  static constexpr size_t kFrames = ULTRON_WINDOW_FRAMES;       /* 100           */
  static constexpr size_t kCoeffs = ULTRON_NUM_COEFFS;          /* 13            */
  static constexpr size_t kFeatureCount = ULTRON_FEATURE_COUNT; /* 1300          */

  /* Allocate the MFCC tables (once, at boot). Returns false if that fails. */
  bool begin();

  void end();

  /* Append captured samples. `hopReady()` becomes true once per 500 ms hop.
   * The flag is a level, not a queue: if the loop ever stalls for more than one
   * hop the extra hops coalesce into a single classified window (the ring still
   * holds the newest 2 s, so no audio is analysed twice). */
  void push(const int16_t *samples, size_t n);

  bool hopReady() const { return hop_ready_; }

  /* Number of classified windows produced so far (monotonic). */
  uint32_t windows() const { return windows_; }

  /* Mean of the most recent 2 s window (used for DC-offset correction). */
  double windowMean() const;

  /* Build the int8 model input for the most recent 2 s window and clear the
   * hop flag. `scale`/`zero_point` come from the interpreter's input tensor.
   * Returns false if the ring has not filled yet (boot transient). */
  bool buildWindowInt8(float scale, int zero_point, int8_t *out);

  size_t mfccContextBytes() const { return mfcc_ctx_bytes_; }

 private:
  mfcc_ctx_t *mfcc_ = nullptr;
  size_t mfcc_ctx_bytes_ = 0;
  size_t write_index_ = 0;
  size_t since_hop_ = 0;
  uint32_t pushed_ = 0;
  uint32_t windows_ = 0;
  bool hop_ready_ = false;
};

}  // namespace ultron

#endif /* ULTRON_FEATURES_H */
