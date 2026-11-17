#ifndef ULTRON_MFCC_H
#define ULTRON_MFCC_H

/*
 * ultron shared C MFCC library — T6 (spec 001-can-hear, NFR-007 DSP clause)
 *
 * Single source of truth for MFCC feature extraction, compiled three ways:
 *   1. Firmware   -> directly into devices/esp32/ultron_voice/   (spec 003)
 *   2. Pi runtime -> shared library loaded via ctypes            (spec 003)
 *   3. Training   -> host build, invoked by `ultron features`    (spec 002)
 *
 * Firmware-ready: only standard C99 headers (stdint, stddef, stdlib, string, math).
 * No POSIX / host-only libc dependencies. All sizing derived from the design
 * parameters below; tables are heap-allocated in mfcc_init() / freed in mfcc_free().
 *
 * Parameters (ported from Edge Impulse MFCC proven in lampara-aliz, per design.md):
 *   - sample rate : 16 kHz, int16 input
 *   - frame       : 20 ms  = 320 samples
 *   - stride      : 20 ms  = 320 samples (no overlap)
 *   - coeffs      : 13 MFCC coefficients
 *   - mel filters : 40 triangular filters, 0 Hz .. sample_rate/2
 *   - FFT size    : 512 (next pow2 >= frame length)
 *   - pre-emphasis: 0.97
 *   - window      : Hamming
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Design parameters (mirrored in tests/test_dsp_parity.py docstring). */
#define MFCC_SAMPLE_RATE 16000
#define MFCC_FRAME_MS     20
#define MFCC_FRAME_LEN    320   /* 20 ms @ 16 kHz */
#define MFCC_NUM_COEFFS   13
#define MFCC_NUM_FILTERS  40
#define MFCC_FFT_SIZE     512

/* Opaque context holding precomputed tables (mel filterbank, DCT, window). */
typedef struct mfcc_ctx mfcc_ctx_t;

/* Allocate context and precompute tables. Returns NULL on allocation failure. */
mfcc_ctx_t *mfcc_init(int sample_rate);

/* Compute one frame of MFCC coefficients.
 * frame:   MFCC_FRAME_LEN int16 samples (20 ms @ 16 kHz)
 * coeffs:  out buffer of at least MFCC_NUM_COEFFS floats
 * Returns 0 on success, non-zero on error.
 */
int mfcc_compute(const mfcc_ctx_t *ctx, const int16_t *frame, float *coeffs);

/* Release context and tables. */
void mfcc_free(mfcc_ctx_t *ctx);

#ifdef __cplusplus
}
#endif

#endif /* ULTRON_MFCC_H */
