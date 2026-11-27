/*
 * features_host_test — host driver for the firmware feature pipeline.
 *
 * Compiles the firmware's own C sources (devices/esp32/ultron_voice/features_core.c,
 * which in turn compiles the shared dsp/mfcc.c) and runs them over a raw int16
 * window, so tests/test_esp32_features.py can compare the firmware's output with
 * the host pipeline (`ultron features` → dsp/mfcc.py, numpy DC fix, exporter
 * quantization) byte for byte.
 *
 * Usage:
 *   features_host_test <input.i16> <coeffs.f32.txt> <int8.bin> <scale> <zero_point>
 *
 *   input.i16      32000 little-endian int16 samples (one 2 s window)
 *   coeffs.f32.txt 1300 lines: one "%0.9g" float per MFCC coefficient, frame-major
 *   int8.bin       1300 int8 values, the model input tensor in NWC order
 */

#include <stdio.h>
#include <stdlib.h>

#include "ultron_features_core.h"

int main(int argc, char **argv) {
  if (argc != 6) {
    fprintf(stderr, "usage: %s <input.i16> <coeffs.f32.txt> <int8.bin> <scale> <zp>\n",
            argv[0]);
    return 2;
  }
  const float scale = (float)atof(argv[4]);
  const int zero_point = atoi(argv[5]);

  static int16_t window[ULTRON_WINDOW_SAMPLES];
  static float coeffs[ULTRON_FEATURE_COUNT];
  static int8_t quantized[ULTRON_FEATURE_COUNT];

  FILE *in = fopen(argv[1], "rb");
  if (in == NULL) {
    perror("input");
    return 1;
  }
  const size_t got = fread(window, sizeof(int16_t), ULTRON_WINDOW_SAMPLES, in);
  fclose(in);
  if (got != ULTRON_WINDOW_SAMPLES) {
    fprintf(stderr, "expected %d samples, got %zu\n", ULTRON_WINDOW_SAMPLES, got);
    return 1;
  }

  const mfcc_ctx_t *ctx = mfcc_init(MFCC_SAMPLE_RATE);
  if (ctx == NULL) {
    fprintf(stderr, "mfcc_init failed\n");
    return 1;
  }
  if (ultron_window_to_mfcc(ctx, window, ULTRON_WINDOW_SAMPLES, coeffs) != 0) {
    fprintf(stderr, "ultron_window_to_mfcc failed\n");
    mfcc_free((mfcc_ctx_t *)ctx);
    return 1;
  }
  ultron_quantize_int8(coeffs, ULTRON_FEATURE_COUNT, scale, zero_point, quantized);

  FILE *feat = fopen(argv[2], "w");
  if (feat == NULL) {
    perror("coeffs");
    mfcc_free((mfcc_ctx_t *)ctx);
    return 1;
  }
  for (size_t i = 0; i < ULTRON_FEATURE_COUNT; i++) {
    fprintf(feat, "%.9g\n", (double)coeffs[i]);
  }
  fclose(feat);

  FILE *q = fopen(argv[3], "wb");
  if (q == NULL) {
    perror("int8");
    mfcc_free((mfcc_ctx_t *)ctx);
    return 1;
  }
  const size_t wrote = fwrite(quantized, sizeof(int8_t), ULTRON_FEATURE_COUNT, q);
  fclose(q);
  mfcc_free((mfcc_ctx_t *)ctx);

  if (wrote != ULTRON_FEATURE_COUNT) {
    fprintf(stderr, "short write on int8 output\n");
    return 1;
  }
  printf("features=%d window=%d mean=%.6f\n", ULTRON_FEATURE_COUNT, ULTRON_WINDOW_SAMPLES,
         ultron_window_mean(window, ULTRON_WINDOW_SAMPLES));
  return 0;
}
