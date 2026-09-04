"""Source fixtures for the provider tests, built through the REAL
parser: ONE PROVIDER'S SLICE of a settings override (the caller
keys it into INFERENCE_SOURCES; `spec` only names the toml section
and changes nothing about the entry, since raw entries are
provider-agnostic). A hand-written dict would be a second
constructor for the shape: it drifts the moment a field is added,
so the test would pass on a shape production never produces."""

from __future__ import annotations

from openbower_kernel.provider_config import RawSource, parse_provider_sources


def source(name: str, base_url: str, *, spec: str = "openai_compatible", api_key: str = "") -> dict[str, RawSource]:
    """One {name: RawSource} entry, parsed from the TOML an operator
    would have written."""
    return parse_provider_sources(f'[[{spec}]]\nname = "{name}"\nbase_url = "{base_url}"\napi_key = "{api_key}"\n')[
        spec
    ]
