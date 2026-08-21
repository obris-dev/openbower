"""The chat-completions SPEC door: any server speaking it, as NAMED
SOURCES (one deploy can run a local Ollama and the canonical vendor
side by side)."""

from __future__ import annotations

from django.conf import settings
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from openbower_kernel.provider_config import ProviderSpec, canonical_base

from .base import ProviderDoor

# The canonical vendor's /v1/models lists EVERYTHING it serves
# (embeddings, audio, images, moderation) with no capability flag, so
# naming is the filter THERE. A custom base takes the roster as served,
# EXCEPT the universal embed hygiene below: a self-hosted server's
# meta-llama/... must not be filtered by vendor prefixes, but an
# embedding model cannot fill a cell anywhere.
_CHAT_PREFIXES = ("gpt-", "chatgpt-", "o1", "o3", "o4", "o5")
_EXCLUDE_PARTS = (
    "embed",
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "whisper",
    "image",
    "dall-e",
    "moderation",
    "instruct",
    "search",
    "codex",
)


class OpenAICompatibleDoor(ProviderDoor):
    CANONICAL_BASE = canonical_base(ProviderSpec.OPENAI_COMPATIBLE)

    @property
    def configured_sources(self) -> dict[str, dict[str, str]]:
        return settings.OPENAI_COMPATIBLE_SOURCES

    def _headers(self, source: dict[str, str]) -> dict[str, str]:
        key = source["api_key"]
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _list_models(self, source: dict[str, str]) -> list[str]:
        rows = self._probe_rows(source, "/models")
        if self._is_canonical(source):
            rows = [
                row
                for row in rows
                if str(row.get("id", "")).startswith(_CHAT_PREFIXES)
                and not any(part in str(row.get("id", "")) for part in _EXCLUDE_PARTS)
            ]
        else:
            # Universal hygiene only: an embedding model cannot fill a
            # cell, whatever serves it (the spec lists them unflagged).
            rows = [row for row in rows if "embed" not in str(row.get("id", ""))]
        # `or 0`: a server can list "created": null, which .get's
        # default does not cover.
        rows.sort(key=lambda row: row.get("created") or 0, reverse=True)
        return [str(row["id"]) for row in rows if row.get("id")]

    def _pydantic_model(self, source: dict[str, str], model_name: str) -> Model:
        provider = OpenAIProvider(base_url=source["base_url"], api_key=self._key_or_placeholder(source))
        return OpenAIChatModel(model_name, provider=provider)


DOOR = OpenAICompatibleDoor()
