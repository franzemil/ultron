"""Python wrapper for the shared C MFCC library (dsp/mfcc.c).

Loads the shared C MFCC library (``build/libmfcc.so``; ``libmfcc.dll`` on
Windows/MinGW) via ``ctypes`` and exposes frame-level MFCC feature extraction. Mirrors the C API in ``dsp/mfcc.h``:

    mfcc_init(16000) -> ctx
    mfcc_compute(ctx, frame[320] int16, coeffs[13] out) -> int
    mfcc_free(ctx)

This is the training/inference feature path: the same C bytes are used on
the host (via this wrapper), on the Orange Pi (shared lib loaded via ctypes),
and compiled directly into the ESP32 firmware. One implementation, everywhere
(principle 2, spec 001).

Parameters (design.md, NFR-007 DSP clause): 16 kHz, int16 input, frame 20 ms
(320 samples), stride 20 ms, 13 MFCC coefficients, 40 mel filters, 512-point
FFT, pre-emphasis 0.97, Hamming window.
"""

from ctypes import CDLL, c_float, c_int, c_int16, c_void_p, POINTER
from pathlib import Path
import subprocess
import sys

_DSP_DIR = Path(__file__).resolve().parent
# Windows (MinGW-w64) shared libraries are .dll; every other host uses .so.
_LIB_NAME = "libmfcc.dll" if sys.platform == "win32" else "libmfcc.so"
_LIB_PATH = _DSP_DIR / "build" / _LIB_NAME

_FRAME_LEN = 320
_NUM_COEFFS = 13


def _build_shared_lib():
    """Build the shared library on demand so the wrapper works from a fresh checkout."""
    subprocess.run(["make", "-C", str(_DSP_DIR), _LIB_NAME], check=True)


class Mfcc:
    """Frame-level MFCC extractor backed by the C library."""

    def __init__(self, sample_rate: int = 16000):
        if not _LIB_PATH.exists():
            _build_shared_lib()
        self._lib = CDLL(str(_LIB_PATH))
        self._lib.mfcc_init.restype = c_void_p
        self._lib.mfcc_init.argtypes = [c_int]
        self._lib.mfcc_compute.restype = c_int
        self._lib.mfcc_compute.argtypes = [
            c_void_p,
            POINTER(c_int16),
            POINTER(c_float),
        ]
        self._lib.mfcc_free.restype = None
        self._lib.mfcc_free.argtypes = [c_void_p]

        self._ctx = self._lib.mfcc_init(sample_rate)
        if not self._ctx:
            raise RuntimeError("mfcc_init() returned NULL")

    def compute(self, frame):
        """Compute 13 MFCC coefficients for one 20 ms frame (320 int16 samples).

        Returns a list of 13 floats. Raises RuntimeError on error.
        """
        if len(frame) != _FRAME_LEN:
            raise ValueError(f"frame must have {_FRAME_LEN} samples, got {len(frame)}")
        in_arr = (c_int16 * _FRAME_LEN)(*[int(v) for v in frame])
        out_arr = (c_float * _NUM_COEFFS)()
        ret = self._lib.mfcc_compute(self._ctx, in_arr, out_arr)
        if ret != 0:
            raise RuntimeError("mfcc_compute() failed")
        return list(out_arr)

    def close(self):
        if self._ctx is not None:
            self._lib.mfcc_free(self._ctx)
            self._ctx = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
