/*
 * features_core — the firmware feature pipeline, in portable C99.
 *
 * This is the *exact* arithmetic the XIAO runs when it turns a 2 s ring-buffer
 * window into the model's `[1, 100, 13]` int8 input tensor:
 *
 *   1. DC-offset correction   (subtract the window mean, round half-to-even,
 *                              clip to int16) — spec 001 FR-022 semantics
 *   2. per-frame MFCC         (shared C library dsp/mfcc.c — never re-implemented)
 *   3. int8 quantization      (round(x / scale) + zero_point, clip [-128, 127])
 *
 * Written as a separate translation unit (no Arduino, no allocation, no I/O) so
 * tests/test_esp32_features.py can compile *these* bytes on the host and compare
 * them against the host feature pipeline (`ultron features` → dsp/mfcc.py +
 * numpy DC fix + export quantization). That keeps the "firmware produces the
 * same feature rows the host produces" guarantee checkable instead of aspirational.
 */

#ifndef ULTRON_FEATURES_CORE_H
#define ULTRON_FEATURES_CORE_H

#include <stddef.h>
#include <stdint.h>

#include "mfcc.h" /* shared C MFCC library (spec 001) — single source of truth */

#ifdef __cplusplus
extern "C" {
#endif

#define ULTRON_WINDOW_FRAMES 100 /* 2.0 s analysis window */
#define ULTRON_FRAME_SAMPLES 320 /* 20 ms frame @ 16 kHz */
#define ULTRON_NUM_COEFFS 13
#define ULTRON_WINDOW_SAMPLES (ULTRON_WINDOW_FRAMES * ULTRON_FRAME_SAMPLES) /* 32000 */
#define ULTRON_FEATURE_COUNT (ULTRON_WINDOW_FRAMES * ULTRON_NUM_COEFFS)     /* 1300 */

/* Mean of a window — the DC offset the host's `dataset dcfix` removes per clip.
 * On device the window *is* the clip, so the correction is per window. */
double ultron_window_mean(const int16_t *samples, size_t n);

/* Write `n` DC-corrected samples into `out` (may alias `in`).
 * Mirrors numpy: rint(samples - mean) clipped to the int16 range, i.e.
 * round-half-to-even — not C's round-half-away-from-zero. */
void ultron_dc_correct(const int16_t *in, size_t n, double mean, int16_t *out);

/* 100 x 13 float MFCC rows for a 2 s window (window_len == 32000 samples).
 * DC correction is applied frame-by-frame with the window mean (same result as
 * correcting the whole window, without a second 64 KB buffer).
 * out must hold ULTRON_FEATURE_COUNT floats. Returns 0 on success. */
int ultron_window_to_mfcc(const mfcc_ctx_t *ctx, const int16_t *window, size_t window_len,
                          float *out);

/* int8 quantization exactly as the exporter's reference interpreter does it
 * (export.py → _run_tflite): round(x / scale) + zero_point, clipped to int8.
 * round() there is numpy's half-to-even, so nearbyint() is the matching C call. */
void ultron_quantize_int8(const float *src, size_t n, float scale, int zero_point, int8_t *dst);

/* NWC (frame-major, 100 x 13) → NCW (coefficient-major, 13 x 100).
 *
 * The features above are always produced in the archive/host layout (frames x
 * coefficients), which is also what `ultron features` writes. Onnx2tf 1.28 /
 * TF 2.21 emit the int8 flatbuffer as NCW `[1, 13, 100]` (measured; the exporter
 * tolerates both layouts), so the firmware transposes the int8 tensor before
 * inference when the model asks for NCW. Kept here — not in infer.cpp — so the
 * host tests can pin the mapping without the TFLM/Arduino stack. */
void ultron_transpose_int8_nwc_to_ncw(const int8_t *nwc, int8_t *ncw, int frames, int coeffs);

#ifdef __cplusplus
}
#endif

#endif /* ULTRON_FEATURES_CORE_H */
