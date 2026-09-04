"""The providers config file: TOML declaring NAMED SOURCES per
provider section, the ONE custody for inference sources (keys live INLINE, the
aws-credentials/npmrc norm for operator-owned, gitignored config; no
env-key mirror, no no-file defaults: two custodies for one fact was
two places for it to drift). Pure parsing here so it is testable
without settings."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit


def vendor_host(url: str) -> str:
    """The HOST, which is what decides whether a base is the vendor's
    own service. Not the whole string: that reads the path and the
    port too, so `<vendor>/v1/beta` and `<vendor>:443/v1` would come
    back non-canonical, and a keyless entry pointed straight at the
    vendor would pass the open test and be called with the SDK's
    placeholder key, which is the one thing this flag exists to
    prevent.

    This also decides the FILL WIDTH: a canonical source is elastic
    and may climb to MAX_FILL_CONCURRENCY, where an undeclared
    self-hosted one stays at one row. So a keyed vendor entry written
    with a path or an explicit port still reads canonical and runs
    the full window, which is the correct reading of what it is, and
    worth knowing before the first fill against it runs wide.

    Host ALONE, deliberately, not scheme and port as well: the vendor
    controls that name, so anything served under it is the vendor
    whatever port or scheme reaches it, and comparing those would
    leave `http://<vendor>/v1` open for exactly the same reason the
    path did. Being wrong in this direction costs a source that
    refuses to run keyless; being wrong the other way calls a vendor
    unauthenticated.

    Never raises. urlsplit rejects unbalanced brackets with a bare
    ValueError, and this is a PREDICATE whose readers judge, never
    refuse (the providers' enrichment, the registry's registration
    guard): an address
    nothing can parse is not the vendor's, and answering that is not
    the same job as refusing a file."""
    try:
        return urlsplit(url.lower()).hostname or ""
    except ValueError:
        return ""


# One third of a model address (provider/source/model); the app's
# serializers enforce the same number, imported from here.
SOURCE_NAME_MAX_LENGTH = 64

# The fill worker's per-fill concurrency ceiling (binary): a RESOURCE
# bound on the SHARED constraints, the Postgres pool and provider
# rate limits, never on the worker's cores (fill rows are IO waits;
# threads sleep on sockets and the per-row CPU is milliseconds, so a
# cores-derived number would measure the wrong machine). Row threads
# release their DB connection before the model call, so held
# connections do not scale with this number.
MAX_FILL_CONCURRENCY = 64


class RawSource(TypedDict):
    """One parsed source, AS THE OPERATOR WROTE IT: the raw entry a
    provider enriches at read (canonical-ness is the provider's own
    fact, derived against its declared vendor origin, so it cannot
    live here)."""

    base_url: str
    api_key: str
    # The OPERATOR's declared fill ceiling. 0 means undeclared, which
    # is absence and never a request for no parallelism: the ceiling
    # then falls to the provider-derived canonical flag.
    concurrency: int


def make_source(*, base_url: str, api_key: str = "", concurrency: int = 0) -> RawSource:
    """THE RawSource constructor. Every source reaches a consumer
    through here, whichever caller produced it (the config file's
    parser, a test fixture), so the base normalization
    happens exactly once. The constructor exists so a field added to
    RawSource cannot strand a construction site."""
    base_url = base_url.strip().rstrip("/")
    return RawSource(base_url=base_url, api_key=api_key, concurrency=concurrency)


class ProviderConfigError(Exception):
    """The file exists but cannot be used; refusing beats a silently
    empty catalog with a config the operator thinks is live."""


def parse_provider_sources(text: str) -> dict[str, dict[str, RawSource]]:
    """{provider name: {source name: RawSource}} from the file's text
    (api_key "" when not inline; concurrency 0 when the source
    declares no ceiling). Section NAMES are taken as written: this
    parser does not know the provider roster, so a typo'd section is
    refused at boot by the registry (which does), never silently
    dropped here."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ProviderConfigError(f"providers config is not valid TOML: {e}") from e
    sources: dict[str, dict[str, RawSource]] = {}
    for section, entries in data.items():
        if not isinstance(entries, list):
            raise ProviderConfigError(f"{section} must be an array of tables ([[{section}]])")
        by_name: dict[str, RawSource] = {}
        for entry in entries:
            # Shape before keys: a non-table entry would raise a bare
            # TypeError out of the key walk, and settings translates
            # only OUR error into words an operator can act on.
            if not isinstance(entry, dict):
                raise ProviderConfigError(f"every [[{section}]] entry must be a table, got {entry!r}")
            unknown_keys = set(entry) - {"name", "base_url", "api_key", "concurrency"}
            if unknown_keys:
                # A typo'd key (apikey = ...) silently closing the
                # source is the silently-empty-catalog outcome this
                # error type exists to prevent.
                raise ProviderConfigError(f"unknown key(s) in a [[{section}]] entry: {', '.join(sorted(unknown_keys))}")
            name = str(entry.get("name", "")).strip()
            base = str(entry.get("base_url", "")).strip().rstrip("/")  # checked below; make_source normalizes
            if not name or not base:
                raise ProviderConfigError(f"every [[{section}]] entry needs name and base_url")
            if not base.startswith(("http://", "https://")):
                raise ProviderConfigError(f"{section} source {name!r} base_url must be http(s), got {base!r}")
            try:
                # The scheme prefix is not enough: urlsplit rejects
                # unbalanced brackets, and a typo like http://[oops/v1
                # clears the prefix check. The whole custody promises
                # OUR error, because a raw one out of settings import
                # names nothing an operator can fix.
                urlsplit(base)
            except ValueError as e:
                raise ProviderConfigError(
                    f"{section} source {name!r} base_url is not a parsable URL ({e}), got {base!r}"
                ) from e
            if len(name) > SOURCE_NAME_MAX_LENGTH:
                raise ProviderConfigError(f"{section} source name {name!r} exceeds {SOURCE_NAME_MAX_LENGTH} chars")
            if not re.fullmatch(r"[A-Za-z0-9_]+", name):
                # Names travel as one third of a model address and as
                # settings keys; a predictable identifier shape keeps
                # every consumer boring.
                raise ProviderConfigError(f"{section} source name {name!r} must be letters, digits, and underscores")
            if name.lower() in {existing.lower() for existing in by_name}:
                # Case-insensitive: two names differing only by case
                # read as one source everywhere humans handle them.
                raise ProviderConfigError(f"duplicate {section} source name (case-insensitive): {name}")
            concurrency = 0
            if "concurrency" in entry:
                declared = entry["concurrency"]
                # bool subclasses int; "concurrency = true" must not
                # slip through as 1.
                if (
                    isinstance(declared, bool)
                    or not isinstance(declared, int)
                    or not 1 <= declared <= MAX_FILL_CONCURRENCY
                ):
                    raise ProviderConfigError(
                        f"{section} source {name!r} concurrency must be an integer "
                        f"from 1 to {MAX_FILL_CONCURRENCY}, got {declared!r}"
                    )
                concurrency = declared
            by_name[name] = make_source(
                base_url=base,
                api_key=str(entry.get("api_key", "") or ""),
                concurrency=concurrency,
            )
        sources[section] = by_name
    return sources


def resolve_provider_sources(path: Path) -> dict[str, dict[str, RawSource]]:
    """The WHOLE custody: the declared file, or nothing when it does
    not exist (no env-key mirror, no no-file defaults; ONE path so
    the fact cannot drift between custodies). Unreadable or invalid
    files RAISE OUR error: Path("") resolves to the working
    DIRECTORY, and a raw IsADirectoryError out of boot names nothing
    the operator can fix."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ProviderConfigError(f"cannot read the providers config at {path}: {e}") from e
    return parse_provider_sources(text)
