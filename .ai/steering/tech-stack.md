# Ultron — Tech Stack

Stack, versions, and **why** each choice was made. Versions marked "pin at implementation" get
recorded with their real numbers in the **PR description** on first use (the changelog is
PR-generated).

## Model & training (source of truth)

The training side is a **conversion chain** — each step exists because the next format cannot be
produced directly:

**PyTorch (train) → ONNX (export bridge) → onnx2tf (convert) → TFLite (deploy to both devices)**

| Choice | Version | Why |
|---|---|---|
| **PyTorch** | 2.x (pin at implementation) | Where the model is defined, trained, and evaluated. This project is deliberately **PyTorch-first** (owner decision) — lampara-aliz trained in TensorFlow/Keras and we want the model source in the ecosystem the owner prefers. The PyTorch model is the source of truth: nothing ships unless the deployed artifact agrees with it. |
| **ONNX** (`torch.onnx.export`) | pin at implementation | The **bridge**. PyTorch cannot emit TFLite directly, so the model is first exported to ONNX — the standard, framework-neutral interchange format. Our model uses only Conv1d/Dense/pooling/softmax, all fully supported, so this step is lossless. |
| **onnx2tf** | pin at implementation | The **converter**: turns the ONNX file into TFLite. Chosen because it is the maintained, standard path (the old `onnx-tensorflow` project is deprecated) and handles shape inference automatically. |
| **TensorFlow** (build-time only) | pin at implementation | onnx2tf internally relies on TensorFlow libraries to *generate* the TFLite file and compute the int8 quantization ranges. Treat it as part of the **compiler toolchain**, not the product: it never trains anything and never runs on either device. |
| **TFLite** (artifacts) | — | The single deployable format both targets consume: the XIAO's TFLite-Micro loads the **int8** flatbuffer; the Pi interpreter loads the **float16/int8** one. Both artifacts come from the same PyTorch checkpoint (FR-010). |
| **uv** | latest | Python environment manager (same as lampara-aliz): one lockfile reproduces the exact training environment on any machine — reproducibility is NFR-006. |

**Why not just train in TensorFlow** (as lampara-aliz did)? Two reasons: the owner explicitly
wants a PyTorch-first pipeline, and keeping training in one framework means one model definition
to maintain — TensorFlow appears only inside the export step, and its output is verified by the
**parity gate** (NFR-007): the TFLite must agree with PyTorch on ≥ 99% of predictions before
anything deploys.

## Deployment targets

| Target | Runtime | Why |
|---|---|---|
| **XIAO ESP32S3 Sense** (ESP32-S3, PDM mic, PSRAM) | TFLite-Micro (Arduino/ESP32 core via `arduino-cli`) | Proven in lampara-aliz: int8 flatbuffers with `AllOpsResolver` run comfortably on the S3. Exact TFLM Arduino library is validated by an early spike (design TD-004). |
| **Orange Pi Zero 2W** (Allwinner H618, ARM64 Cortex-A53, Debian 12) | Python `tflite_runtime` + `sounddevice` | Official aarch64 interpreter wheel; audio capture with a USB mic. Full TensorFlow only as fallback if the wheel is unavailable. |

## DSP

| Choice | Why |
|---|---|
| **Shared C MFCC library** (`dsp/`), one implementation compiled three ways: into XIAO firmware, into the Pi runtime (ctypes), and into the training feature extractor | The single most important lesson from lampara-aliz: training features must be computed by the **same code** that runs at inference, or accuracy silently rots. Parameters ported from the Edge Impulse MFCC that already works there (13 coefficients, 16 kHz, int16 input). |

## Data tooling

| Choice | Why |
|---|---|
| **Typer CLI** (`ultron`) | Same pattern as lampara-aliz's `lampara` CLI: one entry point for dataset, training, export, deploy, bench, EI sync. |
| **ffmpeg** (seeded augmentation) | Proven augmentation recipe from lampara-aliz: gain/tempo/pitch/background-mix/echo variants, fully seeded and reproducible. |
| **numpy** | Feature arrays (`.npz`), quantization representative data. |

## Archive / sync

| Choice | Why |
|---|---|
| **Edge Impulse** (API only, `EI_KEY` in `.env`) | Export-only target: dataset archive + BYOM-style model upload. One-way (`ultron ei sync` → EI). Never a source of model bytes. The lampara project keeps this integration proven. |

## Dev host

macOS arm64. Firmware builds via `arduino-cli` + `esp32:esp32` core (same as lampara-aliz).
Pi software is cross-checked by running it on the Pi itself (aarch64).
