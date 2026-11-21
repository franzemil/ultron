#include "mfcc.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* -------------------------------------------------------------------------- */
/* Constants                                                                  */
/* -------------------------------------------------------------------------- */

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define PRE_EMPHASIS 0.97f
/* Floor for log-energy to avoid log(0). Matches the Python reference exactly. */
#define LOG_EPS 1e-10f

/* -------------------------------------------------------------------------- */
/* Small radix-2 complex FFT (iterative, decimation-in-time).                 */
/* Firmware-safe: no dynamic memory inside the transform; scratch is owned by */
/* the context and preallocated in mfcc_init().                               */
/* -------------------------------------------------------------------------- */

typedef struct {
    float re;
    float im;
} cmplx_t;

static void bit_reverse_copy(cmplx_t *a, int n)
{
    int j = 0;
    for (int i = 0; i < n; i++) {
        if (i < j) {
            cmplx_t t = a[i];
            a[i] = a[j];
            a[j] = t;
        }
        int k = n >> 1;
        while (k & j) {
            j ^= k;
            k >>= 1;
        }
        j ^= k;
    }
}

static void fft_execute(cmplx_t *a, int n, int inverse)
{
    bit_reverse_copy(a, n);
    for (int len = 2; len <= n; len <<= 1) {
        double ang = 2.0 * M_PI / len * (inverse ? 1.0 : -1.0);
        cmplx_t wlen = { (float)cos(ang), (float)sin(ang) };
        for (int i = 0; i < n; i += len) {
            cmplx_t w = { 1.0f, 0.0f };
            int half = len >> 1;
            for (int j = 0; j < half; j++) {
                cmplx_t u = a[i + j];
                cmplx_t v;
                v.re = w.re * a[i + j + half].re - w.im * a[i + j + half].im;
                v.im = w.re * a[i + j + half].im + w.im * a[i + j + half].re;
                a[i + j].re = u.re + v.re;
                a[i + j].im = u.im + v.im;
                a[i + j + half].re = u.re - v.re;
                a[i + j + half].im = u.im - v.im;
                /* w *= wlen */
                float tre = w.re * wlen.re - w.im * wlen.im;
                w.im = w.re * wlen.im + w.im * wlen.re;
                w.re = tre;
            }
        }
    }
    if (inverse) {
        float invn = 1.0f / (float)n;
        for (int i = 0; i < n; i++) {
            a[i].re *= invn;
            a[i].im *= invn;
        }
    }
}

/* -------------------------------------------------------------------------- */
/* Context                                                                    */
/* -------------------------------------------------------------------------- */

struct mfcc_ctx {
    int sample_rate;
    int frame_len;
    int fft_size;
    int num_filters;
    int num_coeffs;

    /* Precomputed Hamming window (frame_len). */
    float *window;
    /* FFT scratch: fft_size complex numbers. */
    cmplx_t *fft_scratch;
    /* Power spectrum magnitude bins: fft_size/2 + 1. */
    float *spectrum;
    /* Mel filterbank: num_filters x (fft_size/2 + 1), row-major. */
    float *filterbank;
    /* DCT-II matrix: num_coeffs x num_filters, row-major (orthonormal). */
    float *dct;
};

/* Hz -> mel (slaney-style, matches librosa / scipy reference). */
static float hz_to_mel(float f)
{
    return 2595.0f * log10f(1.0f + f / 700.0f);
}

/* mel -> Hz. */
static float mel_to_hz(float m)
{
    return 700.0f * (powf(10.0f, m / 2595.0f) - 1.0f);
}

mfcc_ctx_t *mfcc_init(int sample_rate)
{
    if (sample_rate <= 0) {
        return NULL;
    }

    mfcc_ctx_t *ctx = (mfcc_ctx_t *)calloc(1, sizeof(*ctx));
    if (!ctx) {
        return NULL;
    }
    ctx->sample_rate = sample_rate;
    ctx->frame_len = MFCC_FRAME_LEN;
    ctx->fft_size = MFCC_FFT_SIZE;
    ctx->num_filters = MFCC_NUM_FILTERS;
    ctx->num_coeffs = MFCC_NUM_COEFFS;

    int nfft = ctx->fft_size;
    int nfilt = ctx->num_filters;
    int ncoef = ctx->num_coeffs;
    int nspec = nfft / 2 + 1;

    /* --- Hamming window --- */
    ctx->window = (float *)calloc(ctx->frame_len, sizeof(float));
    if (!ctx->window) goto fail;
    for (int i = 0; i < ctx->frame_len; i++) {
        ctx->window[i] = 0.54f - 0.46f * cosf(2.0f * (float)M_PI * (float)i /
                                              (float)(ctx->frame_len - 1));
    }

    /* --- FFT scratch --- */
    ctx->fft_scratch = (cmplx_t *)calloc(nfft, sizeof(cmplx_t));
    if (!ctx->fft_scratch) goto fail;

    /* --- Power spectrum buffer --- */
    ctx->spectrum = (float *)calloc(nspec, sizeof(float));
    if (!ctx->spectrum) goto fail;

    /* --- Mel filterbank (triangular, Slaney) --- */
    ctx->filterbank = (float *)calloc((size_t)nfilt * (size_t)nspec, sizeof(float));
    if (!ctx->filterbank) goto fail;

    float low_mel = hz_to_mel(0.0f);
    float high_mel = hz_to_mel((float)sample_rate / 2.0f);
    float *mel_points = (float *)calloc((size_t)(nfilt + 2), sizeof(float));
    if (!mel_points) goto fail;
    for (int i = 0; i < nfilt + 2; i++) {
        mel_points[i] = low_mel + (high_mel - low_mel) * (float)i / (float)(nfilt + 1);
    }
    int *bin = (int *)calloc((size_t)(nfilt + 2), sizeof(int));
    if (!bin) {
        free(mel_points);
        goto fail;
    }
    for (int i = 0; i < nfilt + 2; i++) {
        bin[i] = (int)floorf((nfft + 1) * mel_to_hz(mel_points[i]) / (float)sample_rate);
    }
    /* Slaney area normalization: 2 / (mel_freq[filter+2] - mel_freq[filter]). */
    for (int m = 0; m < nfilt; m++) {
        int f_left = bin[m];
        int f_c = bin[m + 1];
        int f_right = bin[m + 2];
        float enorm = 2.0f / (mel_to_hz(mel_points[m + 2]) - mel_to_hz(mel_points[m]));
        for (int k = f_left; k < f_c; k++) {
            if (k >= 0 && k < nspec) {
                ctx->filterbank[(size_t)m * nspec + (size_t)k] =
                    enorm * (float)(k - f_left) / (float)(f_c - f_left);
            }
        }
        for (int k = f_c; k < f_right; k++) {
            if (k >= 0 && k < nspec) {
                ctx->filterbank[(size_t)m * nspec + (size_t)k] =
                    enorm * (float)(f_right - k) / (float)(f_right - f_c);
            }
        }
    }
    free(mel_points);
    free(bin);

    /* --- DCT-II matrix (orthonormal), num_coeffs x num_filters --- */
    ctx->dct = (float *)calloc((size_t)ncoef * (size_t)nfilt, sizeof(float));
    if (!ctx->dct) goto fail;
    for (int k = 0; k < ncoef; k++) {
        for (int n = 0; n < nfilt; n++) {
            float val = cosf((float)M_PI * (float)k * (2.0f * (float)n + 1.0f) /
                             (2.0f * (float)nfilt));
            if (k == 0) {
                val *= sqrtf(1.0f / (float)nfilt);
            } else {
                val *= sqrtf(2.0f / (float)nfilt);
            }
            ctx->dct[(size_t)k * nfilt + (size_t)n] = val;
        }
    }

    return ctx;

fail:
    mfcc_free(ctx);
    return NULL;
}

int mfcc_compute(const mfcc_ctx_t *ctx, const int16_t *frame, float *coeffs)
{
    if (!ctx || !frame || !coeffs) {
        return -1;
    }

    int nfft = ctx->fft_size;
    int nspec = nfft / 2 + 1;
    int nfilt = ctx->num_filters;
    int ncoef = ctx->num_coeffs;
    int flen = ctx->frame_len;

    /* 1. DC offset / pre-emphasis + framing. Convert int16 -> float, apply
     * pre-emphasis y[n] = x[n] - 0.97*x[n-1] (x[-1] treated as 0), then
     * Hamming window, zero-padded into FFT scratch. */
    cmplx_t *fft = ctx->fft_scratch;
    float prev = 0.0f;
    for (int i = 0; i < flen; i++) {
        float x = (float)frame[i] / 32768.0f;
        float y = x - PRE_EMPHASIS * prev;
        prev = x;
        fft[i].re = y * ctx->window[i];
        fft[i].im = 0.0f;
    }
    for (int i = flen; i < nfft; i++) {
        fft[i].re = 0.0f;
        fft[i].im = 0.0f;
    }

    /* 2. FFT -> power spectrum (one-sided magnitude squared). */
    fft_execute(fft, nfft, 0);
    for (int k = 0; k < nspec; k++) {
        float re = fft[k].re;
        float im = fft[k].im;
        ctx->spectrum[k] = re * re + im * im;
    }

    /* 3. Mel filterbank energies into a stack buffer.
     *
     * Firmware constraint (spec 003 T12): no dynamic allocation on the
     * inference path. This used to malloc()/free() one 40-float buffer per
     * frame — i.e. 100 allocations per classified window, from the audio loop.
     * 40 floats = 160 B, so a stack array is both smaller and deterministic;
     * the arithmetic below is unchanged, and the host/Pi builds see byte-identical
     * coefficients (tests/test_dsp_parity.py pins that). */
    float energies[MFCC_NUM_FILTERS];
    if (nfilt > MFCC_NUM_FILTERS) {
        return -1;
    }
    for (int m = 0; m < nfilt; m++) {
        float e = 0.0f;
        for (int k = 0; k < nspec; k++) {
            e += ctx->spectrum[k] * ctx->filterbank[(size_t)m * nspec + (size_t)k];
        }
        energies[m] = e;
    }

    /* 4. Log (with floor), in place. */
    for (int m = 0; m < nfilt; m++) {
        float e = energies[m];
        energies[m] = logf(e > LOG_EPS ? e : LOG_EPS);
    }

    /* 5. DCT-II -> first ncoef MFCC coefficients. */
    for (int k = 0; k < ncoef; k++) {
        float acc = 0.0f;
        for (int n = 0; n < nfilt; n++) {
            acc += ctx->dct[(size_t)k * nfilt + (size_t)n] * energies[n];
        }
        coeffs[k] = acc;
    }

    return 0;
}

void mfcc_free(mfcc_ctx_t *ctx)
{
    if (!ctx) return;
    free(ctx->window);
    free(ctx->fft_scratch);
    free(ctx->spectrum);
    free(ctx->filterbank);
    free(ctx->dct);
    free(ctx);
}
