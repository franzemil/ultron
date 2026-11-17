"""KWS network — design.md (002-can-think), restricted op set (Q-008).

Architecture (frozen by design — no freelancing, FR-003/Q-008):

    Conv1d(13 → 16, k=3, relu) → MaxPool1d(2)
    Conv1d(16 → 32, k=3, relu) → GlobalAvgPool1d
    Linear(32 → 16, relu) → Linear(16 → C, softmax)

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


class KwsNet(nn.Module):
    """Small 1-D CNN keyword spotter — dynamic class count C, MCU-sized.

    ~2.9k parameters: small enough for the XIAO arena, large enough for the
    initial 4-class set (design.md "Model").
    """

    def __init__(self, num_classes: int, num_coeffs: int = NUM_COEFFS):
        super().__init__()
        if num_classes < 2:
            raise ValueError(f"num_classes must be ≥ 2, got {num_classes}")
        self.num_classes = num_classes
        self.num_coeffs = num_coeffs
        self.conv1 = nn.Conv1d(num_coeffs, 16, kernel_size=3)
        self.conv2 = nn.Conv1d(16, 32, kernel_size=3)
        self.fc1 = nn.Linear(32, 16)
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
        z = F.adaptive_avg_pool1d(z, 1).squeeze(-1)  # global average pool
        z = F.relu(self.fc1(z))
        return F.softmax(self.fc2(z), dim=1)

    def log_probs(self, x: torch.Tensor) -> torch.Tensor:
        """(N, C) log-probabilities for NLLLoss (see module docstring)."""
        return torch.log(self.forward(x).clamp_min(1e-12))
