"""The messages SPEC door: any server speaking it, as NAMED SOURCES."""

from __future__ import annotations

from django.conf import settings
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from openbower_kernel.provider_config import ProviderSpec, canonical_base

from .base import ProviderDoor

_API_VERSION = "2023-06-01"


class AnthropicCompatibleDoor(ProviderDoor):
    CANONICAL_BASE = canonical_base(ProviderSpec.ANTHROPIC_COMPATIBLE)

    @property
    def configured_sources(self) -> dict[str, dict[str, str]]:
        return settings.ANTHROPIC_COMPATIBLE_SOURCES

    def _headers(self, source: dict[str, str]) -> dict[str, str]:
        headers = {"anthropic-version": _API_VERSION}
        if source["api_key"]:
            headers["x-api-key"] = source["api_key"]
        return headers

    def _list_models(self, source: dict[str, str]) -> list[str]:
        rows = self._probe_rows(source, "/v1/models")
        if not self._is_canonical(source):
            # The same universal hygiene as the openai door: an
            # embedding model cannot fill a cell, whatever serves it.
            rows = [row for row in rows if "embed" not in str(row.get("id", ""))]
        return [str(row["id"]) for row in rows if row.get("id")]

    def _pydantic_model(self, source: dict[str, str], model_name: str) -> Model:
        provider = AnthropicProvider(base_url=source["base_url"], api_key=self._key_or_placeholder(source))
        return AnthropicModel(model_name, provider=provider)


DOOR = AnthropicCompatibleDoor()
