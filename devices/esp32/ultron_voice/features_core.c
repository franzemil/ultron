/* features_core — implementation. See features_core.h. */

#include "features_core.h"

#include <math.h>

/* Compile the shared C MFCC library (dsp/mfcc.c, spec 001) straight into this
 * translation unit. arduino-cli only builds sources that live in the sketch
 * folder, and copying dsp/mfcc.c into the sketch would create a second source
 * of truth that could drift from the host pipeline. Including the repo source
 * makes the firmware and `ultron features` literally the same bytes — the
 * byte-for-byte parity guarantee (tests/test_dsp_parity.py) holds by
 * construction. mfcc.c includes "mfcc.h" relatively, which resolves to dsp/. */
#include "../../../dsp/mfcc.c"

double ultron_window_mean(const int16_t *samples, size_t n) {
  if (samples == NULL || n == 0) return 0.0;
  double sum = 0.0;
  for (size_t i = 0; i < n; i++) {
    sum += (double)samples[i];
  }
  return sum / (double)n;
}

void ultron_dc_correct(const int16_t *in, size_t n, double mean, int16_t *out) {
  if (in == NULL || out == NULL) return;
  for (size_t i = 0; i < n; i++) {
    /* nearbyint() honours the default FE_TONEAREST mode = round half to even,
     * which is what numpy.rint does in the host pipeline. */
    double v = nearbyint((double)in[i] - mean);
    if (v > 32767.0) v = 32767.0;
    if (v < -32768.0) v = -32768.0;
    out[i] = (int16_t)v;
  }
}

int ultron_window_to_mfcc(const mfcc_ctx_t *ctx, const int16_t *window, size_t window_len,
                          float *out) {
  if (ctx == NULL || window == NULL || out == NULL) return -1;
  if (window_len < ULTRON_WINDOW_SAMPLES) return -2;

  const double mean = ultron_window_mean(window, ULTRON_WINDOW_SAMPLES);
  int16_t frame[ULTRON_FRAME_SAMPLES]; /* 640 B — stack, re-used for all frames */

  for (size_t f = 0; f < ULTRON_WINDOW_FRAMES; f++) {
    ultron_dc_correct(window + f * ULTRON_FRAME_SAMPLES, ULTRON_FRAME_SAMPLES, mean, frame);
    float *coeffs = out + f * ULTRON_NUM_COEFFS;
    if (mfcc_compute(ctx, frame, coeffs) != 0) return -3;
  }
  return 0;
}

void ultron_quantize_int8(const float *src, size_t n, float scale, int zero_point, int8_t *dst) {
  if (src == NULL || dst == NULL) return;
  if (!(scale > 0.0f)) { /* a broken scale must not silently emit garbage */
    for (size_t i = 0; i < n; i++) dst[i] = 0;
    return;
  }
  for (size_t i = 0; i < n; i++) {
    double q = nearbyint((double)src[i] / (double)scale) + (double)zero_point;
    if (q > 127.0) q = 127.0;
    if (q < -128.0) q = -128.0;
    dst[i] = (int8_t)q;
  }
}
