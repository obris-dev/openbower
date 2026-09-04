"""The messages SPEC provider: any server speaking it, as NAMED SOURCES."""

from __future__ import annotations

import anthropic
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from .base import InferenceProvider, SourceConfig
from .registry import register

_API_VERSION = "2023-06-01"


class AnthropicCompatibleProvider(InferenceProvider):
    canonical_base = "https://api.anthropic.com"
    timeout_exception = anthropic.APITimeoutError

    def _headers(self, source: SourceConfig) -> dict[str, str]:
        headers = {"anthropic-version": _API_VERSION}
        if source["api_key"]:
            headers["x-api-key"] = source["api_key"]
        return headers

    def _list_models(self, source: SourceConfig) -> list[str]:
        rows = self._probe_rows(source, "/v1/models")
        if not source["canonical"]:
            # The same universal hygiene as the openai provider: an
            # embedding model cannot fill a cell, whatever serves it.
            rows = [row for row in rows if "embed" not in str(row.get("id", ""))]
        return [str(row["id"]) for row in rows if row.get("id")]

    def _pydantic_model(self, source: SourceConfig, model_name: str) -> Model:
        provider = AnthropicProvider(base_url=source["base_url"], api_key=self._key_or_placeholder(source))
        return AnthropicModel(model_name, provider=provider)


PROVIDER = AnthropicCompatibleProvider()

register(PROVIDER)
