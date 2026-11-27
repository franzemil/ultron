#!/usr/bin/env python3
"""Embed a TFLite-Micro flatbuffer as ``devices/esp32/ultron_voice/model_data.h``.

Task: spec 003-can-act **T12**. The firmware needs the model as a
``const unsigned char[]`` for `tflite::GetModel()`; the artifact itself
(``*.tflite``) is gitignored (AGENTS rule 2), so this script is the committed,
deterministic bridge:

    build/model_int8.tflite  --(gen_esp32_model.py)-->  model_data.h

The generated header carries its own provenance so a flash can never be
mistaken for something it is not:

* ``ULTRON_MODEL_PLACEHOLDER`` — 1 when the embedded bytes are the deterministic
  placeholder (random weights, see ``gen_esp32_placeholder.py``), 0 for the
  trained spec-002 artifact. The firmware prints it in the boot banner
  (``DBG mem … placeholder=<0|1>``).
* ``ULTRON_MODEL_PROVENANCE`` — free-text provenance printed at boot.
* the sha256 + byte size of the flatbuffer, and the input/output signature
  (verified with ``tensorflow`` when that optional dependency is installed).

Guards (fail loud, never emit a header that cannot work):

* model must be ≤ 100 KB (NFR-002 int8 flash budget);
* model must be int8 with input ``[1, 100, 13]`` and one score per frozen class
  (100 frames × 13 coefs, spec 002 TD-006/TD-012) — checked at generation time
  when tensorflow is available, and at boot otherwise.

Usage:
    uv run ultron esp32 placeholder          # make the placeholder
    uv run ultron esp32 model --placeholder
    uv run ultron esp32 model --model build/model_int8.tflite \
        --provenance "spec 002 T9 trained artifact"
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root (cli/src/ultron/)
DEFAULT_MODEL = REPO_ROOT / "build/model_int8.tflite"
DEFAULT_OUT = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src/model_data.h"
DEFAULT_CONFIG = REPO_ROOT / "devices/esp32/libraries/UltronVoice/src/ultron_config.h"
DEFAULT_PLACEHOLDER_MODEL = REPO_ROOT / "build/placeholder/model_int8.tflite"

# NFR-002: int8 model ≤ 100 KB.
INT8_FLASH_BUDGET_BYTES = 100 * 1024
# Contract dimensions (spec 002 TD-006: 100 frames x 13 coefficients).
EXPECTED_FRAMES = 100
EXPECTED_COEFFS = 13

PLACEHOLDER_PROVENANCE = (
    "PLACEHOLDER random weights - reference_model() seed 42, logit_scale 8, "
    "synthetic calibration; NOT the spec-002 trained artifact"
)


class ModelDataError(RuntimeError):
    """Loud, actionable failure — no half-valid header is ever written."""


def read_config_classes(config_path: Path) -> list[str]:
    """Extract ULTRON_CLASS_NAMES from the generated config.h (single source)."""
    if not config_path.is_file():
        raise ModelDataError(
            f"{config_path} not found — run `uv run ultron esp32 config` first"
        )
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r"ULTRON_CLASS_NAMES\[[^\]]*\]\s*=\s*\{([^}]*)\}", text)
    if not match:
        raise ModelDataError(f"{config_path}: ULTRON_CLASS_NAMES not found")
    names = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(1))
    if not names:
        raise ModelDataError(f"{config_path}: ULTRON_CLASS_NAMES is empty")
    return names


def inspect_model(model_bytes: bytes) -> dict:
    """Introspect the flatbuffer with tensorflow when available (best effort)."""
    info: dict = {"verified": False, "n_classes": None, "frames": None, "coeffs": None}
    try:
        import numpy as np  # noqa: F401
        import tensorflow as tf
    except Exception:  # noqa: BLE001 — optional dependency (uv sync --extra export)
        return info
    interpreter = tf.lite.Interpreter(model_content=model_bytes)
    interpreter.allocate_tensors()
    input_detail = interpreter.get_input_details()[0]
    output_detail = interpreter.get_output_details()[0]
    in_shape = tuple(int(d) for d in input_detail["shape"])
    out_shape = tuple(int(d) for d in output_detail["shape"])
    info.update(
        verified=True,
        dtype=str(input_detail["dtype"].__name__),
        input_shape=in_shape,
        input_nwc=in_shape[1:] == (EXPECTED_FRAMES, EXPECTED_COEFFS),
        input_ncw=in_shape[1:] == (EXPECTED_COEFFS, EXPECTED_FRAMES),
        output_shape=out_shape,
        frames=in_shape[1] if in_shape[1:] == (EXPECTED_FRAMES, EXPECTED_COEFFS) else (
            in_shape[2] if len(in_shape) == 3 else None
        ),
        coeffs=in_shape[2] if in_shape[1:] == (EXPECTED_FRAMES, EXPECTED_COEFFS) else (
            in_shape[1] if len(in_shape) == 3 else None
        ),
        n_classes=out_shape[-1] if len(out_shape) == 2 else None,
    )
    return info


def _c_bytes(model_bytes: bytes, per_line: int = 12) -> list[str]:
    lines = []
    for start in range(0, len(model_bytes), per_line):
        chunk = model_bytes[start : start + per_line]
        lines.append("  " + ", ".join(f"0x{b:02x}" for b in chunk) + ",")
    return lines


def render(
    model_bytes: bytes,
    *,
    model_path: Path,
    classes: list[str],
    placeholder: bool,
    provenance: str,
    info: dict,
) -> str:
    sha = hashlib.sha256(model_bytes).hexdigest()
    try:  # keep the header machine-independent
        display_path = model_path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        display_path = model_path.name
    lines: list[str] = []
    w = lines.append
    w("/*")
    w(" * model_data.h — GENERATED FILE, DO NOT EDIT.")
    w(" *")
    w(" * Embedded TFLite-Micro flatbuffer for devices/esp32/ultron_voice (T12).")
    w(" * Regenerate with:")
    w(" *")
    w(" *     uv run ultron esp32 model")
    w(" *")
    w(f" * model        : {display_path}")
    w(f" * model sha256 : {sha}")
    w(f" * model bytes  : {len(model_bytes)} (NFR-002 budget {INT8_FLASH_BUDGET_BYTES})")
    w(f" * placeholder  : {1 if placeholder else 0}")
    w(f" * provenance   : {provenance}")
    w(f" * classes      : {' '.join(classes)}")
    if info.get("verified"):
        layout = "NWC [1,100,13]" if info.get("input_nwc") else "NCW [1,13,100]"
        w(f" * input        : {info.get('input_shape')} {info.get('dtype')} — {layout}")
        w(f" * output       : {info.get('output_shape')}")
    else:
        w(" * input/output : NOT verified at generation time (tensorflow absent) —")
        w(" *                the firmware validates [1,100,13] int8 at boot instead.")
    w(" */")
    w("")
    w("#ifndef ULTRON_MODEL_DATA_H")
    w("#define ULTRON_MODEL_DATA_H")
    w("")
    w(f"#define ULTRON_MODEL_PLACEHOLDER {1 if placeholder else 0}")
    w(f'#define ULTRON_MODEL_PROVENANCE "{provenance}"')
    w(f'#define ULTRON_MODEL_SHA256 "{sha}"')
    w(f"#define ULTRON_MODEL_FRAMES {EXPECTED_FRAMES}")
    w(f"#define ULTRON_MODEL_COEFFS {EXPECTED_COEFFS}")
    w(f"#define ULTRON_MODEL_CLASS_COUNT {len(classes)}")
    w(f"static const char *const ULTRON_MODEL_CLASSES[{len(classes)}] = {{" +
      ", ".join(f'"{c}"' for c in classes) + "};")
    w("")
    w("/* Flatbuffer bytes — .rodata, read directly by tflite::GetModel(). */")
    w("alignas(16) static const unsigned char g_model_data[] = {")
    lines.extend(_c_bytes(model_bytes))
    w("};")
    w("static const unsigned int g_model_data_len = sizeof(g_model_data);")
    w("")
    w("#endif /* ULTRON_MODEL_DATA_H */")
    w("")
    return "\n".join(lines)


def generate(
    model_path: Path,
    out_path: Path,
    config_path: Path,
    *,
    placeholder: bool,
    provenance: str | None,
    check: bool,
) -> str:
    if not model_path.is_file():
        raise ModelDataError(
            f"model not found: {model_path} — run `uv run ultron esp32 placeholder` "
            "(placeholder) or `uv run ultron export tflite` (trained artifact)"
        )
    model_bytes = model_path.read_bytes()
    if not model_bytes:
        raise ModelDataError(f"{model_path} is empty")
    if len(model_bytes) > INT8_FLASH_BUDGET_BYTES:
        raise ModelDataError(
            f"{model_path}: {len(model_bytes)} bytes exceeds the NFR-002 int8 budget "
            f"({INT8_FLASH_BUDGET_BYTES} bytes)"
        )
    if model_bytes[4:8] != b"TFL3":
        raise ModelDataError(f"{model_path}: not a TFLite flatbuffer (missing TFL3 identifier)")

    classes = read_config_classes(config_path)
    info = inspect_model(model_bytes)
    if info.get("verified"):
        if not (info.get("input_nwc") or info.get("input_ncw")):
            raise ModelDataError(
                f"{model_path}: input signature {info.get('input_shape')} is neither "
                f"[1, {EXPECTED_FRAMES}, {EXPECTED_COEFFS}] (NWC) nor "
                f"[1, {EXPECTED_COEFFS}, {EXPECTED_FRAMES}] (NCW) — spec 002 TD-006"
            )
        if info.get("n_classes") != len(classes):
            raise ModelDataError(
                f"{model_path}: {info.get('n_classes')} output classes but the frozen class "
                f"order has {len(classes)} ({classes}) — retrain + re-export (TD-012)"
            )

    text = render(
        model_bytes,
        model_path=model_path,
        classes=classes,
        placeholder=placeholder,
        provenance=provenance or (PLACEHOLDER_PROVENANCE if placeholder else "unspecified"),
        info=info,
    )
    if check:
        current = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
        if current != text:
            raise ModelDataError(
                f"{out_path} is stale — regenerate with `uv run ultron esp32 model`"
            )
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="TFLite flatbuffer (default: build/placeholder/model_int8.tflite with "
        "--placeholder, else build/model_int8.tflite)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--placeholder",
        action="store_true",
        help="mark the embedded bytes as the deterministic placeholder (not trained)",
    )
    parser.add_argument("--provenance", type=str, default=None)
    parser.add_argument("--check", action="store_true", help="fail if the header is stale")
    args = parser.parse_args(argv)

    model_path = args.model
    if model_path is None:
        model_path = DEFAULT_PLACEHOLDER_MODEL if args.placeholder else DEFAULT_MODEL

    try:
        text = generate(
            model_path,
            args.out,
            args.config,
            placeholder=args.placeholder,
            provenance=args.provenance,
            check=args.check,
        )
    except ModelDataError as exc:
        print(f"gen_esp32_model: {exc}", file=sys.stderr)
        return 1

    if args.check:
        print(f"gen_esp32_model: {args.out} is up to date")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"gen_esp32_model: wrote {args.out} from {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
