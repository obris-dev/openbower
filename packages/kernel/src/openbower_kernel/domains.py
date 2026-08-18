"""Domain normalization: the app's one spelling of domain identity.

Agreement with the web normalizer is pinned by the shared fixture
(fixtures/domain_normalization.json); the data service's canonicalizer
is a superset-tolerant sibling (values this refuses cannot exist as
universe keys, so extra strictness only saves doomed lookups)."""

from __future__ import annotations

import re

import idna

# Any scheme, mirroring the web side (URLs pasted from anywhere).
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def _is_canonical_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and not (len(part) > 1 and part[0] == "0") and int(part) <= 255 for part in parts)


# LDH hostname labels (RFC 952/1123): 1-63 chars, no leading/trailing
# hyphen. Stricter than the data service's normalizer is SAFE: values
# this refuses (spaces, commas, underscores) cannot exist as universe
# keys, so refusing them here only saves a doomed lookup.
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
# DNS's own bound on a full name; anything longer cannot be a real
# domain, so it is unusable as a key.
MAX_DOMAIN_LENGTH = 253


def normalize_domain(raw: str) -> str:
    """Lowercased bare ASCII domain: scheme, userinfo, `www.`, path,
    query, fragment, port, trailing dot, and surrounding whitespace
    stripped; internationalized names IDNA-encoded (the punycode form is
    what DNS, certificates, and the crawl data all use). Returns "" for
    input that leaves nothing usable (caller decides how to treat
    unusable values)."""
    value = raw.strip().lower()
    value = _SCHEME_RE.sub("", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # user:pass@host is still a host reference; the port is not.
    value = value.rsplit("@", 1)[-1].split(":", 1)[0]
    value = value.removeprefix("www.").rstrip(".")
    if not value.isascii():
        # UTS-46 (the WHATWG URL profile the web normalizer gets from
        # the platform), not the stdlib's IDNA2003 codec: the two
        # disagree on characters like sharp s.
        try:
            value = idna.encode(value, uts46=True).decode("ascii")
        except idna.IDNAError:
            return ""
    # A bare TLD, over-long name, or non-hostname junk ("acme inc.com",
    # "a,b.com") is not a usable domain key.
    if "." not in value or len(value) > MAX_DOMAIN_LENGTH or not _HOSTNAME.match(value):
        return ""
    # A numeric last label exists only on IP literals, and only the
    # canonical dotted-quad form is accepted: URL parsers REWRITE the
    # shorthand/hex/octal forms ("1.1", "0x7f.0.0.1"), so both
    # normalizers refuse them identically instead.
    if value.rsplit(".", 1)[-1].isdigit() and not _is_canonical_ipv4(value):
        return ""
    return value
