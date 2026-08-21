"""The providers config file: TOML declaring NAMED SOURCES per spec
door, the ONE custody for inference sources (keys live INLINE, the
aws-credentials/npmrc norm for operator-owned, gitignored config; no
env-key mirror, no no-file defaults: two custodies for one fact was
two places for it to drift). Pure parsing here so it is testable
without settings."""

from __future__ import annotations

import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple


class ProviderSpec(StrEnum):
    """The API-spec doors (SPECS, never companies): the ONE home for
    their names. The agents app's AgentProvider IS this enum (the
    config file's vocabulary and the model column are the same fact)."""

    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"


class _SpecInfo(NamedTuple):
    canonical_base: str


# One declaration per spec: its vendor origin (whose keyless sources
# stay closed).
_SPEC_INFO: dict[ProviderSpec, _SpecInfo] = {
    ProviderSpec.OPENAI_COMPATIBLE: _SpecInfo("https://api.openai.com/v1"),
    ProviderSpec.ANTHROPIC_COMPATIBLE: _SpecInfo("https://api.anthropic.com"),
}


def canonical_base(spec: str) -> str:
    """The spec's vendor origin (keyless sources there stay CLOSED)."""
    return _SPEC_INFO[ProviderSpec(spec)].canonical_base


# One third of a model address (provider/source/model); the app's
# serializers enforce the same number, imported from here.
SOURCE_NAME_MAX_LENGTH = 64


class ProviderConfigError(Exception):
    """The file exists but cannot be used; refusing beats a silently
    empty catalog with a config the operator thinks is live."""


def parse_provider_sources(text: str) -> dict[str, dict[str, dict[str, str]]]:
    """{spec: {name: {base_url, api_key}}} from the file's text (api_key
    "" when not inline). Unknown top-level keys are rejected (a typo'd
    spec name must not vanish silently)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ProviderConfigError(f"providers config is not valid TOML: {e}") from e
    unknown = set(data) - {spec.value for spec in ProviderSpec}
    if unknown:
        raise ProviderConfigError(f"unknown provider spec(s) in config: {', '.join(sorted(unknown))}")
    sources: dict[str, dict[str, dict[str, str]]] = {}
    for spec in ProviderSpec:
        entries = data.get(spec.value, [])
        if not isinstance(entries, list):
            raise ProviderConfigError(f"{spec.value} must be an array of tables ([[{spec.value}]])")
        by_name: dict[str, dict[str, str]] = {}
        for entry in entries:
            unknown_keys = set(entry) - {"name", "base_url", "api_key"}
            if unknown_keys:
                # A typo'd key (apikey = ...) silently closing the
                # source is the silently-empty-catalog outcome this
                # error type exists to prevent.
                raise ProviderConfigError(
                    f"unknown key(s) in a [[{spec.value}]] entry: {', '.join(sorted(unknown_keys))}"
                )
            name = str(entry.get("name", "")).strip()
            base = str(entry.get("base_url", "")).strip().rstrip("/")
            if not name or not base:
                raise ProviderConfigError(f"every [[{spec.value}]] entry needs name and base_url")
            if not base.startswith(("http://", "https://")):
                raise ProviderConfigError(f"{spec.value} source {name!r} base_url must be http(s), got {base!r}")
            if len(name) > SOURCE_NAME_MAX_LENGTH:
                raise ProviderConfigError(f"{spec.value} source name {name!r} exceeds {SOURCE_NAME_MAX_LENGTH} chars")
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                # Names travel as one third of a model address and as
                # settings keys; a predictable identifier shape keeps
                # every consumer boring.
                raise ProviderConfigError(f"{spec.value} source name {name!r} must be letters, digits, and underscores")
            if name.lower() in {existing.lower() for existing in by_name}:
                # Case-insensitive: two names differing only by case
                # read as one source everywhere humans handle them.
                raise ProviderConfigError(f"duplicate {spec.value} source name (case-insensitive): {name}")
            by_name[name] = {"base_url": base, "api_key": str(entry.get("api_key", "") or "")}
        sources[spec.value] = by_name
    return sources


def resolve_provider_sources(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """The WHOLE custody: the declared file, or every spec empty when
    it does not exist (no env-key mirror, no no-file defaults; ONE
    path so the fact cannot drift between custodies). Unreadable or
    invalid files RAISE OUR error: Path("") resolves to the working
    DIRECTORY, and a raw IsADirectoryError out of boot names nothing
    the operator can fix."""
    if not path.exists():
        return {spec.value: {} for spec in ProviderSpec}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProviderConfigError(f"cannot read the providers config at {path}: {e}") from e
    return parse_provider_sources(text)
