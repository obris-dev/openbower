"""The tools config file: TOML declaring VENDOR CREDENTIALS (one
single-table section per vendor, keys INLINE, the
aws-credentials/npmrc norm for operator-owned, gitignored config; no
env-key mirror, no no-file defaults: two custodies for one fact was
two places for it to drift) plus a [tools] section wiring each tool
to the vendor that serves it. Pure parsing here so it is testable
without settings."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import NamedTuple

# The wiring section's reserved name. Every other top-level table is a
# vendor's credentials; this one maps tool names to serving vendors.
WIRING_SECTION = "tools"


class ToolConfig(NamedTuple):
    """The parsed file: vendor credential tables AS WRITTEN plus the
    tool wiring AS WRITTEN. Which names are real vendors and tools,
    and which keys a vendor needs, are the registries' facts, judged
    at boot where the rosters are known, never here."""

    vendor_keys: dict[str, dict[str, str]]
    wiring: dict[str, str]


class ToolConfigError(Exception):
    """The file exists but cannot be used; refusing beats silently
    gated tools with a config the operator thinks is live."""


def parse_tool_config(text: str) -> ToolConfig:
    """ToolConfig from the file's text. Shape only: every section a
    single table of string values ([tools] included). Section and key
    NAMES are taken as written: this parser does not know the vendor
    or tool rosters, so a typo'd name is refused at boot by the
    registry gate (which does), never silently dropped here."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ToolConfigError(f"tools config is not valid TOML: {e}") from e
    vendor_keys: dict[str, dict[str, str]] = {}
    wiring: dict[str, str] = {}
    for section, entries in data.items():
        if not isinstance(entries, dict):
            # A bare top-level key (login = "l" above any [section])
            # is a stranded credential no vendor will ever read.
            raise ToolConfigError(f"{section} must be a table ([{section}]) of string values, got {entries!r}")
        for key, value in entries.items():
            if not isinstance(value, str):
                # A quoted value is the one shape every credential and
                # every wiring target has; anything else (a number, a
                # nested table, a list) is a typo'd file.
                raise ToolConfigError(f"[{section}] {key} must be a string, got {value!r}")
        if section == WIRING_SECTION:
            wiring = dict(entries)
        else:
            vendor_keys[section] = dict(entries)
    return ToolConfig(vendor_keys=vendor_keys, wiring=wiring)


def resolve_tool_config(path: Path) -> ToolConfig:
    """The WHOLE custody: the declared file, or nothing when it does
    not exist (no env-key mirror, no no-file defaults; ONE path so
    the fact cannot drift between custodies). Unreadable or invalid
    files RAISE OUR error: Path("") resolves to the working
    DIRECTORY, and a raw IsADirectoryError out of boot names nothing
    the operator can fix."""
    if not path.exists():
        return ToolConfig(vendor_keys={}, wiring={})
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ToolConfigError(f"cannot read the tools config at {path}: {e}") from e
    return parse_tool_config(text)
