"""KWS network — design.md (002-can-think), restricted op set (Q-008).

Architecture (A-002 amendment of the design's frozen figure, 2026-11-19):

    Conv1d(13 → 16, k=3, relu) → MaxPool1d(2)
    Conv1d(16 → 32, k=3, relu) → mean+std pool over time (64 features)
    Dropout(0.2) → Linear(64 → 16, relu) → Linear(16 → C, softmax)

Why the pool changed from GlobalAvgPool: averaging alone discards how the
energy is distributed over the window, and that is exactly what separates the
short commands (``la`` = "luz alta" vs ``lb`` = "luz baja"). Measured on the
project archive with clip-grouped 5-fold CV (no augmentation leaks):
``mean`` 87.8% vs ``mean+std`` 90.2% vs ``mean+std+mean|Δ|`` 90.8% for la/lb.
Std is computed as ``E[x²] − E[x]²`` with ``mean``/`mul`/`sqrt` only, so the
ONNX → onnx2tf → int8 path keeps the T9 op-set restriction (Q-008) and the
99% parity gate.

``C`` — the number of output classes — is **dynamic** (TD-012 incremental
rollout): the class set is derived per training run from the archive labels
(FR-032), so the same class builds 3 classes for the minimum viable set
(``wake``, ``noise``, 1 command) up to the registry cap of 13.

Input is the archive layout ``(N, 100, 13)`` — N rows of one 2 s analysis
window (100 frames × 13 MFCC coefficients, TD-006) — transposed internally to
``(N, 13, 100)`` for Conv1d (coefficients are the channels). The softmax
stays **inside** the graph so the exported artifact (T9) emits probabilities
the runtime thresholds directly (FR-015, Q-006); training therefore consumes
log-probs (:meth:`KwsNet.log_probs` + NLLLoss) instead of stacking a second
softmax on top of this one.
"""

from __future__ import annotations

import torch
from torch import nn
import torch.nn.functional as F

# Feature layout (dsp/mfcc.h + cli/src/ultron/features.py).
NUM_COEFFS = 13
NUM_FRAMES = 100  # 2.0 s window at 20 ms frames
DROPOUT = 0.2  # A-002: the net was memorising 216 clips (in-sample 100%)


def pool_stats(z: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Mean and standard deviation over the time axis, int8-safe.

    ``z`` is ``(N, C, T)``. The variance is computed **centered** — subtract the
    mean, then square — because the algebraic ``E[x²] − E[x]²`` form subtracts two
    large nearly-equal numbers, and full-int8 quantization turns that cancellation
    into garbage (measured: 13/1000 argmax flips and exact 0.5/0.5 ties, while the
    centred form keeps the T9 parity gate green).

    Ops used: ``mean``, ``sub``, ``mul``, ``sqrt``, ``clamp`` — all inside the
    Q-008 restricted set that onnx2tf handles.
    """
    mean = z.mean(dim=-1)  # (N, C) — no squeeze: onnx2tf mis-transposes Squeeze (T9)
    centered = z - mean.unsqueeze(-1)  # broadcast (N, C, 1)
    # Mean absolute deviation instead of std: same temporal-spread information
    # (89.8% vs 90.2% la/lb separability in CV) but no `sqrt`, whose infinite
    # derivative at 0 amplified int8 quantization error into parity failures.
    spread = centered.abs().mean(dim=-1)  # (N, C)
    return mean, spread


class KwsNet(nn.Module):
    """Small 1-D CNN keyword spotter — dynamic class count C, MCU-sized.

    ~3.3k parameters: small enough for the XIAO arena and for the initial
    4-class set (design.md "Model", amended by A-002: mean+std pool + dropout).
    """

    def __init__(self, num_classes: int, num_coeffs: int = NUM_COEFFS):
        super().__init__()
        if num_classes < 2:
            raise ValueError(f"num_classes must be ≥ 2, got {num_classes}")
        self.num_classes = num_classes
        self.num_coeffs = num_coeffs
        self.conv1 = nn.Conv1d(num_coeffs, 16, kernel_size=3)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=3)
        self.dropout = nn.Dropout(DROPOUT)
        self.fc1 = nn.Linear(32 * 2, 16)  # mean + std over time
        self.fc2 = nn.Linear(16, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(N, 100, 13) float32 → (N, C) softmax probabilities."""
        if x.ndim != 3 or x.shape[2] != self.num_coeffs:
            raise ValueError(
                f"expected input (N, {NUM_FRAMES}, {self.num_coeffs}), got {tuple(x.shape)}"
            )
        z = x.transpose(1, 2)  # (N, coeffs, frames) — coefficients are channels
        z = F.relu(self.conv1(z))
        z = F.max_pool1d(z, 2)
        z = F.relu(self.conv2(z))
        # mean+std pool: onnx2tf-safe (T9) — reduce over the time axis only, then
        # concatenate. Variance is computed CENTERED (subtract first, then square):
        # the textbook E[x²] − E[x]² form cancels catastrophically once activations
        # are int8-quantized (E[x²] ~ 1300 vs E[x]² ~ 64), which broke the ≥99%
        # parity gate with exact 0.5/0.5 output ties.
        mean, std = pool_stats(z)
        z = torch.cat([mean, std], dim=1)  # (N, 64)
        z = self.dropout(z)
        z = F.relu(self.fc1(z))
        return F.softmax(self.fc2(z), dim=1)

    def log_probs(self, x: torch.Tensor) -> torch.Tensor:
        """(N, C) log-probabilities for NLLLoss (see module docstring)."""
        return torch.log(self.forward(x).clamp_min(1e-12))
