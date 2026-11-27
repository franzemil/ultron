/* features — implementation. See features.h. */

#include "ultron_features.h"

#include <Arduino.h>

namespace ultron {

namespace {
/* Static storage: never heap, never stack (the loop() task stack is ~8 KB). */
int16_t g_ring[FeatureExtractor::kRingSamples]; /* 64 KB — 2.0 s @ 16 kHz int16 */
int16_t g_frame[FeatureExtractor::kFrameSamples]; /* 640 B frame scratch          */
float g_mfcc[FeatureExtractor::kFeatureCount];    /* 5.2 KB (100 x 13)            */
}  // namespace

bool FeatureExtractor::begin() {
  if (mfcc_ != nullptr) return true;
  const size_t heap_before = ESP.getFreeHeap();
  mfcc_ = mfcc_init(MFCC_SAMPLE_RATE);
  if (mfcc_ == nullptr) return false;
  const size_t heap_after = ESP.getFreeHeap();
  mfcc_ctx_bytes_ = heap_before > heap_after ? heap_before - heap_after : 0;
  write_index_ = 0;
  since_hop_ = 0;
  pushed_ = 0;
  windows_ = 0;
  hop_ready_ = false;
  return true;
}

void FeatureExtractor::end() {
  if (mfcc_ == nullptr) return;
  mfcc_free(mfcc_);
  mfcc_ = nullptr;
}

void FeatureExtractor::push(const int16_t *samples, size_t n) {
  if (samples == nullptr) return;
  for (size_t i = 0; i < n; i++) {
    g_ring[write_index_] = samples[i];
    write_index_ = (write_index_ + 1) % kRingSamples;
    pushed_++;
    since_hop_++;
    if (since_hop_ >= kHopSamples) {
      since_hop_ = 0;
      hop_ready_ = true;
    }
  }
}

double FeatureExtractor::windowMean() const {
  /* Mean over the 2 s window, straight out of the ring (no window copy).
   * Cheap: 32 k adds per 500 ms hop. */
  double sum = 0.0;
  for (size_t i = 0; i < kRingSamples; i++) {
    sum += (double)g_ring[(write_index_ + i) % kRingSamples];
  }
  return sum / (double)kRingSamples;
}

bool FeatureExtractor::buildWindowInt8(float scale, int zero_point, int8_t *out) {
  if (mfcc_ == nullptr || out == nullptr) return false;
  /* A full 2 s window must have been captured before anything is classified. */
  if (pushed_ < kRingSamples) {
    hop_ready_ = false;
    return false;
  }

  /* DC-offset correction uses the *window* mean (the host corrects per clip;
   * on device the 2 s window is the clip). Same constant for every frame. */
  const double mean = windowMean();

  /* Oldest sample of the window sits exactly at write_index_: the ring is full
   * and the next write overwrites the oldest sample. */
  for (size_t f = 0; f < kFrames; f++) {
    const size_t base = (write_index_ + f * kFrameSamples) % kRingSamples;
    if (base + kFrameSamples <= kRingSamples) {
      memcpy(g_frame, g_ring + base, kFrameSamples * sizeof(int16_t));
    } else {
      const size_t head = kRingSamples - base;
      memcpy(g_frame, g_ring + base, head * sizeof(int16_t));
      memcpy(g_frame + head, g_ring, (kFrameSamples - head) * sizeof(int16_t));
    }
    ultron_dc_correct(g_frame, kFrameSamples, mean, g_frame);
    if (mfcc_compute(mfcc_, g_frame, g_mfcc + f * kCoeffs) != 0) {
      hop_ready_ = false;
      return false;
    }
  }

  ultron_quantize_int8(g_mfcc, kFeatureCount, scale, zero_point, out);
  hop_ready_ = false;
  windows_++;
  return true;
}

}  // namespace ultron
