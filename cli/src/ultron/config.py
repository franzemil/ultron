"""Application configuration.

Loads :file:`.env` (gitignored — never commit secrets) and exposes the
Edge Impulse key used by later ``ultron ei`` commands.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

# Load .env if present (never fails when it is absent — ci/cd injects secrets
# through the environment instead).
load_dotenv()


class Config:
    """Runtime configuration sourced from environment variables."""

    def __init__(self) -> None:
        self.ei_api_key: str = os.getenv("EI_API_KEY", "")
        self.ei_project: str | None = os.getenv("EI_PROJECT")

    @property
    def configured(self) -> bool:
        """True when an Edge Impulse API key is available."""
        return bool(self.ei_api_key)


config = Config()
