"""DSP parity test — T6 (spec 001-can-hear, NFR-007 DSP clause).

Compares the shared C MFCC library (dsp/mfcc.c, exposed via the dsp/mfcc.py
ctypes wrapper) against a documented, pinned reference implementation on a
per-frame basis, asserting max abs diff <= 1e-3 per frame.

Why a pinned reference instead of the lampara-aliz EI vectors:
  The lampara-aliz Edge Impulse extraction (the spec's reference-vectors
  source) is not reachable from this session (git ls-remote fails, no network
  path to github.com/lampara-aliz). Per issue #9 guidance we therefore build
  the parity harness against a reference implementation implemented here and
  state it explicitly, rather than silently substituting.

  Pinned reference implementation: NumPy 2.5.4 (FFT, mel filterbank, DCT-II).
  The NumPy reference mirrors the C algorithm exactly (same pre-emphasis 0.97,
  Hamming window, 512-point FFT, 40 Slaney triangular mel filters 0..8000 Hz,
  log floor 1e-10, orthonormal DCT-II, first 13 coefficients), so agreement
  is limited only by float32 (C) vs float64 (NumPy) rounding — well within
  1e-3.

  The lampara-aliz cross-check is left as an UNCHECKED checkbox in PR #15, and
  a comment has been posted on issue #9 asking the owner for the reference
  vectors so this test can be extended to compare against them directly.

Scope: per-frame MFCC parity only. The analysis window (2 s / 100 frames,
500 ms hop) lives in the feature extractor (`ultron features`, spec 002) and is
out of scope for this frame-level parity test.
"""

import wave
from pathlib import Path

import numpy as np
import pytest

from dsp.mfcc import Mfcc

# ---- Pinned reference versions (stated per issue #9 guidance) ----
NUMPY_VERSION = "2.5"  # exact patch used for recorded parity numbers: 2.5.4

# ---- MFCC parameters (design.md; must match dsp/mfcc.h) ----
SAMPLE_RATE = 16000
FRAME_LEN = 320          # 20 ms @ 16 kHz
FRAME_MS = 20
STRIDE_MS = 20           # stride == frame, no overlap
NUM_COEFFS = 13
NUM_FILTERS = 40
FFT_SIZE = 512
PREEMPHASIS = 0.97
LOG_EPS = 1e-10
LOW_HZ = 0.0
HIGH_HZ = SAMPLE_RATE / 2.0


def hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def reference_mfcc(frame_int16):
    """NumPy reference MFCC mirroring dsp/mfcc.c exactly (float64)."""
    x = frame_int16.astype(np.float64) / 32768.0
    # Pre-emphasis: y[n] = x[n] - 0.97*x[n-1], x[-1] = 0
    y = np.empty_like(x)
    y[0] = x[0]
    y[1:] = x[1:] - PREEMPHASIS * x[:-1]
    # Hamming window
    n = np.arange(FRAME_LEN)
    window = 0.54 - 0.46 * np.cos(2.0 * np.pi * n / (FRAME_LEN - 1))
    framed = y * window
    # Zero-pad to FFT_SIZE, power spectrum (one-sided)
    spec = np.fft.fft(framed, FFT_SIZE)
    power = np.abs(spec[: FFT_SIZE // 2 + 1]) ** 2
    # Slaney triangular mel filterbank
    low = hz_to_mel(LOW_HZ)
    high = hz_to_mel(HIGH_HZ)
    mel_points = np.linspace(low, high, NUM_FILTERS + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((FFT_SIZE + 1) * hz_points / SAMPLE_RATE).astype(int)
    filterbank = np.zeros((NUM_FILTERS, FFT_SIZE // 2 + 1))
    for m in range(NUM_FILTERS):
        f_left, f_c, f_right = bins[m], bins[m + 1], bins[m + 2]
        enorm = 2.0 / (mel_to_hz(mel_points[m + 2]) - mel_to_hz(mel_points[m]))
        for k in range(f_left, f_c):
            filterbank[m, k] = enorm * (k - f_left) / (f_c - f_left)
        for k in range(f_c, f_right):
            filterbank[m, k] = enorm * (f_right - k) / (f_right - f_c)
    energies = power @ filterbank.T
    # Log with floor
    log_e = np.log(np.maximum(energies, LOG_EPS))
    # Orthonormal DCT-II, first NUM_COEFFS
    kk = np.arange(NUM_COEFFS)[:, None]
    nn = np.arange(NUM_FILTERS)[None, :]
    dct = np.cos(np.pi * kk * (2.0 * nn + 1.0) / (2.0 * NUM_FILTERS))
    dct[0] *= np.sqrt(1.0 / NUM_FILTERS)
    dct[1:] *= np.sqrt(2.0 / NUM_FILTERS)
    return dct @ log_e


def make_signal():
    """Deterministic test audio (seed 42): voiced sines + broadband noise."""
    rng = np.random.default_rng(42)
    t = np.arange(3 * SAMPLE_RATE) / SAMPLE_RATE  # 3 s
    sig = (
        0.4 * np.sin(2 * np.pi * 300 * t)
        + 0.3 * np.sin(2 * np.pi * 700 * t)
        + 0.2 * np.sin(2 * np.pi * 1200 * t)
        + 0.1 * rng.standard_normal(t.size)
    )
    sig = np.clip(sig, -1.0, 1.0)
    return (sig * 32767.0).astype(np.int16)


def frames_of(signal):
    """Yield non-overlapping 20 ms frames (stride == frame)."""
    for start in range(0, len(signal) - FRAME_LEN + 1, FRAME_LEN):
        yield signal[start : start + FRAME_LEN]


@pytest.fixture
def mfcc():
    with Mfcc(SAMPLE_RATE) as m:
        yield m


def test_numpy_version_stated():
    """Guard: the NumPy reference version line is recorded and matched.

    NFR-006 reproducibility: the reference implementation is NumPy 2.5.x
    (NUMPY_VERSION records the line; the exact patch used for the recorded
    parity numbers is 2.5.4, the version this harness was built against).
    Patch releases of NumPy do not change FFT/mel/DCT numerics materially,
    so the guard enforces the major.minor line rather than the patch.
    """
    import importlib
    v = importlib.metadata.version("numpy")
    assert v.rsplit(".", 1)[0] == NUMPY_VERSION, (
        f"numpy {v} is outside the documented reference line {NUMPY_VERSION}.x"
    )


def test_per_frame_parity(mfcc):
    """Per-frame max abs diff between C MFCC and NumPy reference <= 1e-3."""
    signal = make_signal()
    max_abs_diff = 0.0
    n_frames = 0
    for frame in frames_of(signal):
        c_coeffs = np.array(mfcc.compute(frame), dtype=np.float64)
        ref_coeffs = reference_mfcc(frame)
        diff = float(np.max(np.abs(c_coeffs - ref_coeffs)))
        max_abs_diff = max(max_abs_diff, diff)
        n_frames += 1
    assert n_frames > 0, "no frames extracted"
    # NFR-007 DSP clause: per-frame max abs diff <= 1e-3
    assert max_abs_diff <= 1e-3, (
        f"parity failed: max abs diff {max_abs_diff:.2e} > 1e-3 over {n_frames} frames"
    )


# ---- parity on real device recordings (T28 / US-002) -----------------------
#
# The test above proves the C library agrees with the pinned reference on
# synthetic audio. T28 additionally proves it on the audio the *device* wrote:
# a XIAO PDM capture has a different noise floor, a possible DC offset and full
# 5 s takes instead of 3 s, so any int16-rounding or framing assumption in the C
# path that the synthetic signal happens not to hit would surface here.
#
# The recordings live under dataset/ (gitignored, AGENTS rule 2), so the test
# skips when no device clip is present — CI stays green, and the owner's machine
# runs the real check after the recording pass:
#
#     uv run pytest tests/test_dsp_parity.py -q -s -k device
DEVICE_DATASET_ROOT = Path(__file__).resolve().parents[1] / "dataset" / "originals"


def device_recordings():
    """XIAO-tagged clips ingested into dataset/originals/<label>/."""
    if not DEVICE_DATASET_ROOT.is_dir():
        return []
    return sorted(DEVICE_DATASET_ROOT.glob("*/*_xiao_*.wav"))


def read_wav_int16(path):
    """Return the samples of a dataset clip, asserting the dataset format."""
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    if (rate, channels, sampwidth) != (SAMPLE_RATE, 1, 2):
        raise AssertionError(
            f"{path}: expected {SAMPLE_RATE} Hz / 16-bit / mono, "
            f"got {rate} Hz / {sampwidth * 8}-bit / {channels}ch"
        )
    return np.frombuffer(raw, dtype="<i2")


def test_per_frame_parity_on_device_recordings(mfcc):
    """C MFCC vs the pinned reference on every XIAO-tagged device recording."""
    clips = device_recordings()
    if not clips:
        pytest.skip(
            "no XIAO device recordings under dataset/originals "
            "(run the T28 recording pass; dataset/ is gitignored)"
        )

    per_clip = []
    worst = 0.0
    total_frames = 0
    for path in clips:
        signal = read_wav_int16(path)
        clip_max = 0.0
        clip_frames = 0
        for frame in frames_of(signal):
            c_coeffs = np.array(mfcc.compute(frame), dtype=np.float64)
            diff = float(np.max(np.abs(c_coeffs - reference_mfcc(frame))))
            clip_max = max(clip_max, diff)
            clip_frames += 1
        per_clip.append((path.name, clip_frames, clip_max))
        worst = max(worst, clip_max)
        total_frames += clip_frames

    print("\ndevice-recording MFCC parity (C library vs NumPy reference):")
    print(f"  {'clip':<28} {'frames':>7}  {'max abs diff':>12}")
    for name, frames, diff in per_clip:
        print(f"  {name:<28} {frames:>7}  {diff:>12.3e}")
    print(f"  {'TOTAL':<28} {total_frames:>7}  {worst:>12.3e}")

    assert total_frames > 0, "device recordings produced no frames"
    assert worst <= 1e-3, (
        f"device-recording parity failed: max abs diff {worst:.2e} > 1e-3 "
        f"over {total_frames} frames from {len(per_clip)} clip(s)"
    )
