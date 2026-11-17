"""Application configuration.

Loads :file:`.env` (gitignored — never commit secrets) via pydantic-settings
and exposes the Edge Impulse key used by later ``ultron ei`` commands.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Runtime configuration sourced from environment variables / :file:`.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ei_api_key: str = ""
    ei_project: str | None = None

    @property
    def configured(self) -> bool:
        """True when an Edge Impulse API key is available."""
        return bool(self.ei_api_key)


config = Config()
