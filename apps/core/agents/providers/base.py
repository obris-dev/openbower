"""The door contract: every provider is an API SPEC (never a company)
holding NAMED SOURCES, and every door behaves identically on custody
and caching, so those live here. A door owns exactly what the
framework does not: WHICH sources are open (custody), WHAT each one
serves (the roster probe), and HOW to construct the pydantic-ai Model
for an address; the loop, tool schemas, and structured output are
pydantic-ai's. The ABC is what forces a new door to implement the
full seam."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import httpx
from pydantic_ai.models import Model

from openbower_kernel.provider_config import SourceConfig

from ..constants import LIST_TIMEOUT_SECONDS, MODEL_MAX_LENGTH, PROBE_FAILURE_TTL_SECONDS

logger = logging.getLogger(__name__)


class ProviderDoor(ABC):
    def __init__(self) -> None:
        # Rosters change server-side, not per process: probe once per
        # source and keep the answer (a restart refreshes). ONLY
        # successful probes cache: caching a boot-race timeout would
        # blank the catalog until restart, and start-compose-open-app
        # is the primary local flow. Benign under threads (str-keyed
        # dict ops; a double probe last-writes equivalent data).
        self._roster_cache: dict[str, list[str]] = {}
        self._probe_failed_at: dict[str, float] = {}

    @property
    @abstractmethod
    def configured_sources(self) -> dict[str, SourceConfig]:
        """The door's settings entry: {name: SourceConfig}. A property,
        never cached at init, so test overrides apply."""

    @abstractmethod
    def _headers(self, source: SourceConfig) -> dict[str, str]:
        """The spec's auth/version headers for one source."""

    @abstractmethod
    def _list_models(self, source: SourceConfig) -> list[str]:
        """The live roster call for one OPEN source; raise on trouble
        (models() turns it into an honest empty)."""

    @abstractmethod
    def _pydantic_model(self, source: SourceConfig, model_name: str) -> Model:
        """The pydantic-ai Model for one OPEN source + model name."""

    def source_config(self, name: str) -> SourceConfig | None:
        """One configured source, None when the name is unknown."""
        return self.configured_sources.get(name)

    def _is_open(self, source: SourceConfig) -> bool:
        # Keyless is fine on someone's own server; at the vendor origin
        # it is not, and nothing calls that unauthed.
        return bool(source["api_key"]) or not source["canonical"]

    def sources(self) -> list[str]:
        """The door's OPEN source names, in env order."""
        return [name for name, source in self.configured_sources.items() if self._is_open(source)]

    def models(self, source_name: str) -> list[str]:
        source = self.source_config(source_name)
        if source is None or not self._is_open(source):
            return []
        cached = self._roster_cache.get(source_name)
        if cached is not None:
            return cached
        failed_at = self._probe_failed_at.get(source_name)
        if failed_at is not None and time.monotonic() - failed_at < PROBE_FAILURE_TTL_SECONDS:
            # A short NEGATIVE TTL, not a cache: the boot race heals on
            # the next probe after it, while a dead source stops
            # costing a serial timeout on every request in between.
            return []
        try:
            names = self._list_models(source)
        except Exception as e:
            logger.warning("%s models failed (%s, %s): %s", type(self).__name__, source_name, type(e).__name__, e)
            self._probe_failed_at[source_name] = time.monotonic()
            return []
        self._probe_failed_at.pop(source_name, None)
        oversize = [n for n in names if len(n) > MODEL_MAX_LENGTH]
        if oversize:
            # A name past the serializer's bound would be offered by
            # the picker and then refused at Save; dropping it HERE
            # keeps every offered address saveable.
            logger.warning(
                "%s: dropping %d oversize model name(s) from %s", type(self).__name__, len(oversize), source_name
            )
            names = [n for n in names if len(n) <= MODEL_MAX_LENGTH]
        # NO fallback floor anywhere: the most common failed canonical listing is
        # a bad key, and a hardcoded floor would hide it behind models
        # that can never run. An empty roster is the honest answer.
        self._roster_cache[source_name] = names
        return names

    def _probe_rows(self, source: SourceConfig, path: str) -> list[dict]:
        """The shared probe shell: GET the roster path, raise on
        non-200 (distinct from an honestly-empty 200)."""
        response = httpx.get(f"{source['base_url']}{path}", headers=self._headers(source), timeout=LIST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            raise ValueError(f"roster probe returned {response.status_code}")
        return response.json().get("data", [])

    @staticmethod
    def _key_or_placeholder(source: SourceConfig) -> str:
        # The SDK refuses a missing key even where the server ignores
        # it; keyless open sources (local servers) get a placeholder.
        return source["api_key"] or "unused"

    def pydantic_model(self, source_name: str, model_name: str) -> Model | None:
        """The runnable Model for an address, or None when the source
        is unknown or CLOSED (the runtime writes blank cells, never
        calls a vendor unauthed)."""
        source = self.source_config(source_name)
        if source is None or not self._is_open(source):
            return None
        return self._pydantic_model(source, model_name)
