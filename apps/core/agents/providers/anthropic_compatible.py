"""The messages SPEC door: any server speaking it, as NAMED SOURCES."""

from __future__ import annotations

import anthropic
from django.conf import settings
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from openbower_kernel.provider_config import SourceConfig

from .base import ProviderDoor

# See the sibling door: the SDK's own timeout type, not httpx's.
TIMEOUT_EXCEPTION: type[Exception] = anthropic.APITimeoutError

_API_VERSION = "2023-06-01"


class AnthropicCompatibleDoor(ProviderDoor):
    @property
    def configured_sources(self) -> dict[str, SourceConfig]:
        return settings.ANTHROPIC_COMPATIBLE_SOURCES

    def _headers(self, source: SourceConfig) -> dict[str, str]:
        headers = {"anthropic-version": _API_VERSION}
        if source["api_key"]:
            headers["x-api-key"] = source["api_key"]
        return headers

    def _list_models(self, source: SourceConfig) -> list[str]:
        rows = self._probe_rows(source, "/v1/models")
        if not source["canonical"]:
            # The same universal hygiene as the openai door: an
            # embedding model cannot fill a cell, whatever serves it.
            rows = [row for row in rows if "embed" not in str(row.get("id", ""))]
        return [str(row["id"]) for row in rows if row.get("id")]

    def _pydantic_model(self, source: SourceConfig, model_name: str) -> Model:
        provider = AnthropicProvider(base_url=source["base_url"], api_key=self._key_or_placeholder(source))
        return AnthropicModel(model_name, provider=provider)


DOOR = AnthropicCompatibleDoor()
