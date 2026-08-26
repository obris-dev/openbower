"""Source fixtures for the provider tests, built through the REAL
parser. A settings override wants {name: SourceConfig}, and a
hand-written dict is a second constructor for that shape: it drifts
the moment a field is added, and worse, it can declare a field (the
canonical flag) that disagrees with what the parser would derive from
the same base_url, so the test would pass on a shape production never
produces."""

from __future__ import annotations

from openbower_kernel.provider_config import ProviderSpec, SourceConfig, parse_provider_sources


def source(
    name: str, base_url: str, *, spec: str = ProviderSpec.OPENAI_COMPATIBLE, api_key: str = ""
) -> dict[str, SourceConfig]:
    """One {name: SourceConfig} entry, parsed from the TOML an operator
    would have written."""
    return parse_provider_sources(f'[[{spec}]]\nname = "{name}"\nbase_url = "{base_url}"\napi_key = "{api_key}"\n')[
        spec
    ]
