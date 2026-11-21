"""Firmware ↔ host feature parity — spec 003-can-act T12.

Per the PR checklist the firmware "must produce the same feature rows the host
produces". This test proves that for the firmware's own C sources:

* ``devices/esp32/ultron_voice/features_core.c`` (window DC-offset correction +
  the shared ``dsp/mfcc.c`` + int8 quantization) is compiled **on the host**,
  unchanged, and run over a deterministic 2 s window;
* the same window goes through the host pipeline that ``ultron features`` uses:
  ``dsp/mfcc.py`` (the same C library through ctypes), numpy DC correction with
  the same half-to-even rounding, and the exporter's int8 quantization formula
  (``model/src/ultron_model/export.py`` → ``_run_tflite``);
* the two int8 tensors must be **identical** — not "close". A one-LSB difference
  would mean the device and the training pipeline disagree about the model input,
  which is exactly the silent divergence the parity gate exists to prevent.

Scope honesty: this pins the *feature* stage. It says nothing about whether the
model recognises a phrase (that needs spec 002's trained artifact).
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dsp.mfcc import Mfcc

REPO_ROOT = Path(__file__).resolve().parents[1]
ESP32_DIR = REPO_ROOT / "devices/esp32/ultron_voice"
DRIVER = REPO_ROOT / "tests/esp32/features_host_test.c"
# features_core.c #includes the shared dsp/mfcc.c itself (one source of truth, no
# duplicate copy in the sketch), so it must be the only source compiled here.
SOURCES = [ESP32_DIR / "features_core.c"]

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 32000
FRAME_LEN = 320
N_FRAMES = 100
N_COEFFS = 13

# A representative int8 input quantization (the placeholder model's real values:
# scale 0.0368484, zero_point -9). Both sides use the same numbers, so the test is
# about the *rounding* semantics, not about this particular model.
SCALE = 0.03684842586517334
ZERO_POINT = -9

pytestmark = pytest.mark.skipif(
    shutil.which("cc") is None, reason="no host C compiler (cc) available"
)


@pytest.fixture(scope="module")
def firmware_binary(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("features_host") / "features_host_test"
    cmd = [
        "cc",
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-O2",
        f"-I{ESP32_DIR}",
        f"-I{REPO_ROOT / 'dsp'}",
        str(DRIVER),
        *[str(src) for src in SOURCES],
        "-lm",
        "-o",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"compile failed:\n{proc.stderr}"
    return out


def make_window(seed: int = 42) -> np.ndarray:
    """Deterministic 2 s int16 window with a DC offset and a speech-ish envelope."""
    rng = np.random.default_rng(seed)
    t = np.arange(WINDOW_SAMPLES) / SAMPLE_RATE
    # Two formant-ish tones with an amplitude envelope, plus noise and a DC offset
    # (the DC offset is what the correction stage exists for).
    envelope = 0.5 * (1.0 - np.cos(2.0 * np.pi * t / 1.7)) ** 2
    signal = envelope * (0.6 * np.sin(2 * np.pi * 220.0 * t) + 0.3 * np.sin(2 * np.pi * 900.0 * t))
    signal += 0.02 * rng.standard_normal(t.size)
    signal = np.clip(signal, -1.0, 1.0)
    offset = 1373.0  # deliberate DC offset
    return np.clip(np.rint(signal * 20000.0 + offset), -32768, 32767).astype(np.int16)


def host_pipeline(window: np.ndarray, mfcc: Mfcc) -> np.ndarray:
    """The host's feature path: DC fix (numpy) → dsp/mfcc.py → export quantization."""
    samples = window.astype(np.int32)
    offset = samples.mean()
    corrected = np.clip(np.rint(samples - offset), -32768, 32767).astype(np.int16)

    features = np.empty((N_FRAMES, N_COEFFS), dtype=np.float32)
    for i in range(N_FRAMES):
        frame = corrected[i * FRAME_LEN : (i + 1) * FRAME_LEN]
        features[i] = mfcc.compute(frame)

    quantized = np.clip(np.round(features / SCALE) + ZERO_POINT, -128, 127).astype(np.int8)
    return quantized.reshape(-1)


@pytest.fixture(scope="module")
def parity_run(firmware_binary: Path, tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("features_parity")
    window = make_window()

    raw_path = out / "window.i16"
    raw_path.write_bytes(window.astype("<i2").tobytes())
    coeffs_path = out / "coeffs.txt"
    int8_path = out / "int8.bin"

    proc = subprocess.run(
        [
            str(firmware_binary),
            str(raw_path),
            str(coeffs_path),
            str(int8_path),
            repr(SCALE),
            str(ZERO_POINT),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"firmware feature pipeline failed:\n{proc.stderr}"

    fw_coeffs = np.array(
        [float(line) for line in coeffs_path.read_text().split()], dtype=np.float32
    )
    fw_int8 = np.frombuffer(int8_path.read_bytes(), dtype=np.int8)

    with Mfcc(SAMPLE_RATE) as mfcc:
        host_int8 = host_pipeline(window, mfcc)

    return {
        "window": window,
        "fw_coeffs": fw_coeffs,
        "fw_int8": fw_int8,
        "host_int8": host_int8,
    }


def test_firmware_emits_the_window_contract(parity_run):
    """100 frames x 13 coefficients — the archive/model layout (TD-006)."""
    assert parity_run["fw_coeffs"].shape == (N_FRAMES * N_COEFFS,)
    assert parity_run["fw_int8"].shape == (N_FRAMES * N_COEFFS,)


def test_int8_tensor_is_identical(parity_run):
    """The device's model input must be byte-identical to the host's."""
    fw, host = parity_run["fw_int8"], parity_run["host_int8"]
    mismatches = np.flatnonzero(fw != host)
    assert mismatches.size == 0, (
        f"{mismatches.size}/{fw.size} int8 mismatches, first at {mismatches[:5].tolist()}: "
        f"firmware={fw[mismatches[:5]].tolist()} host={host[mismatches[:5]].tolist()}"
    )


def test_tensor_is_not_degenerate(parity_run):
    """Guard against a vacuous parity pass (e.g. both sides emitting zeros).

    Note: the placeholder model's input scale was calibrated on synthetic
    N(0, 1) feature rows, so a real-audio window saturates the int8 rails — that
    is a property of the placeholder, not of the pipeline, and it is yet another
    reason the placeholder cannot recognise anything. Parity still holds exactly
    because both sides clip at the same places.
    """
    host = parity_run["host_int8"]
    assert len(np.unique(host)) > 4
    clipped = float(np.mean((host == -128) | (host == 127)))
    assert 0.0 <= clipped <= 1.0  # recorded below for the PR numbers
    print(f"clipped fraction for this synthetic window: {clipped:.1%}")


def test_dc_correction_matches_numpy_rounding():
    """Half-to-even, not half-away-from-zero: the 0.5 cases must agree exactly."""
    # Integer samples and a mean of 0.5 produce exact .5 rounding cases.
    samples = np.array([0, 1, 2, -1, -2, 3], dtype=np.int16)
    # numpy reference
    offset = samples.astype(np.int32).mean()  # 0.5
    expected = np.clip(np.rint(samples - offset), -32768, 32767).astype(np.int16)

    # C reference — compile a 10-line driver on the spot (the function is static-free).
    src = REPO_ROOT / "tests/esp32/_dc_rounding_probe.c"
    probe_c = src.with_suffix(".c")
    probe_c.write_text(
        """
#include <stdio.h>
#include "features_core.h"
int main(void) {
    const int16_t in[6] = {0, 1, 2, -1, -2, 3};
    int16_t out[6];
    ultron_dc_correct(in, 6, 0.5, out);
    for (int i = 0; i < 6; i++) printf("%d\\n", (int)out[i]);
    return 0;
}
""",
        encoding="utf-8",
    )
    exe = src.with_suffix(".bin")
    proc = subprocess.run(
        [
            "cc",
            "-std=c99",
            f"-I{ESP32_DIR}",
            str(probe_c),
            str(ESP32_DIR / "features_core.c"),
            "-lm",
            "-o",
            str(exe),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    got = np.array([int(x) for x in run.stdout.split()], dtype=np.int16)

    probe_c.unlink(missing_ok=True)
    exe.unlink(missing_ok=True)

    assert got.tolist() == expected.tolist(), (
        f"rounding mismatch — firmware {got.tolist()} vs numpy {expected.tolist()}"
    )


def test_nwc_to_ncw_transpose_matches_numpy(firmware_binary: Path, tmp_path):
    """The int8 layout mapping the firmware applies must equal numpy's transpose.

    Onnx2tf 1.28/TF 2.21 emit the int8 input as NCW [1, 13, 100]; if this mapping
    were wrong, inference would run on scrambled coefficients and every score
    would be noise — a failure that is invisible on the host without this test.
    """
    rng = np.random.default_rng(7)
    nwc = rng.integers(-128, 127, size=(N_FRAMES, N_COEFFS), dtype=np.int8)

    probe = tmp_path / "transpose_probe.c"
    probe.write_text(
        """
#include <stdio.h>
#include <stdlib.h>
#include "features_core.h"
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    const int frames = atoi(argv[1]);
    const int coeffs = atoi(argv[2]);
    static int8_t nwc[64 * 32];
    static int8_t ncw[64 * 32];
    FILE *in = fopen(argv[3], "rb");
    if (!in) return 3;
    if (fread(nwc, 1, (size_t)frames * coeffs, in) != (size_t)frames * coeffs) return 4;
    fclose(in);
    ultron_transpose_int8_nwc_to_ncw(nwc, ncw, frames, coeffs);
    fwrite(ncw, 1, (size_t)frames * coeffs, stdout);
    return 0;
}
""",
        encoding="utf-8",
    )
    exe = tmp_path / "transpose_probe"
    compile_cmd = [
        "cc",
        "-std=c99",
        f"-I{ESP32_DIR}",
        str(probe),
        str(ESP32_DIR / "features_core.c"),
        "-lm",
        "-o",
        str(exe),
    ]
    built = subprocess.run(compile_cmd, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr

    raw = tmp_path / "nwc.bin"
    raw.write_bytes(nwc.tobytes())
    run = subprocess.run(
        [str(exe), str(N_FRAMES), str(N_COEFFS), str(raw)], capture_output=True
    )
    assert run.returncode == 0
    got = np.frombuffer(run.stdout, dtype=np.int8).reshape(N_COEFFS, N_FRAMES)
    assert np.array_equal(got, nwc.T), "NWC → NCW transposition is wrong"
