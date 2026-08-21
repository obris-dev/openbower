"""URL grounding: the only URLs a cell may carry are ones that actually
appeared in evidence or in the RENDERED PROMPT (row-fed URLs are the
user's own ground truth, which is what lets a tool-less agent echo a
{{website}} legitimately); anything else is a fabrication and is
dropped (a blank over a plausible-looking lie). Grounding applies to
EVERY string field and to URLs EMBEDDED in prose, whatever the
declared output type, and to SCHEME-LESS spellings WITH a path
(www.acme.com/x, acme.com/team): small local models drop schemes
constantly, and a text field is not a fabrication loophole. A bare
domain without a path is a FACT, www-led or not (judging www.acme.com
while passing acme.com would blank a row-fed domain the model echoed
with www. added)."""

from __future__ import annotations

import re

from ..constants import DEFAULT_PEOPLE_SITE

# A URL anywhere in free text: schemed, or a bare domain WITH a path
# whose final label is TLD-SHAPED (alpha, 2+): "4.5/5" and "Scored
# 3.5/5" are ratios, not link claims, and a digit-labeled match here
# blanked exactly the answers users trust a blank to mean "no
# evidence" about. Trailing
# sentence punctuation and a closing bracket stay outside the body:
# evidence lines read "title :: snippet [url]", so models are PRIMED
# to bracket URLs, and a bracket that ended the lookahead would
# smuggle the URL past grounding entirely.
_TAIL = r"[^\s\]]+?(?=[.,;)\]]*(?:\s|$))"
_TEXT_URL = re.compile(
    rf"https?://{_TAIL}|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{{2,}}/{_TAIL}",
    re.IGNORECASE,
)
# The POOL builder is PERMISSIVE where the detector is strict: it
# reads the USER'S OWN prompt, so admitting a bare or www-led domain
# legitimizes nothing new, while missing one blanks an honest schemed
# echo of row-fed ground truth (small models add schemes routinely).
_POOL_URL = re.compile(
    rf"https?://{_TAIL}|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{{2,}}(?:/{_TAIL})?",
    re.IGNORECASE,
)
# The regional-subdomain collapse exists BECAUSE of the contacts tool:
# its domain derives from the people site so the two cannot drift.
_PEOPLE_DOMAIN = DEFAULT_PEOPLE_SITE.split("/")[0]
# The tracking vocabulary stripped at comparison time (the utm_
# family plus the big click ids); anything else in a query names a
# distinct resource. trk/originalSubdomain are the PEOPLE SITE'S own
# litter and strip only there: on any other host they may be real
# identity.
_TRACKING_PARAM = re.compile(r"^(?:utm_[a-z0-9_]*|fbclid|gclid)=", re.IGNORECASE)
_PEOPLE_TRACKING_PARAM = re.compile(r"^(?:trk|originalsubdomain)=", re.IGNORECASE)
_REGIONAL = re.compile(rf"^[a-z]{{2,3}}\.{re.escape(_PEOPLE_DOMAIN)}\b")


def canonical_url(url: str) -> str:
    """The comparison form: lowercase, scheme and trailing slash
    stripped, and the people site's regional subdomains collapsed
    (www./uk./au. serve the same profile slug and models mix them
    freely)."""
    value = url.strip().lower()
    value = re.sub(r"^https?://", "", value)
    # Fragments are navigation litter, always stripped. The query
    # keeps everything but KNOWN tracking params: stripping it whole
    # collided watch?v=A with watch?v=B, dropping the second hit from
    # evidence and rewriting a model URL to a DIFFERENT real resource
    # (worse than the promised blank).
    value = value.split("#", 1)[0]
    path, _, query = value.partition("?")
    path = path.rstrip("/")
    host = path.split("/", 1)[0]
    people = host == _PEOPLE_DOMAIN or host.endswith("." + _PEOPLE_DOMAIN)
    query = "&".join(
        pair
        for pair in query.split("&")
        if pair and not _TRACKING_PARAM.match(pair) and not (people and _PEOPLE_TRACKING_PARAM.match(pair))
    )
    value = f"{path}?{query}" if query else path
    value = _REGIONAL.sub(_PEOPLE_DOMAIN, value)
    value = value.removeprefix("www.")
    return value


def has_url(value: str) -> bool:
    """Whether grounding has anything to judge in this value."""
    return _TEXT_URL.search(value) is not None


def allowed_urls(urls: list[str], prompt: str = "") -> dict[str, str]:
    """canonical form -> the SOURCE'S version of the URL (a cell always
    receives the hit's or the row's rendering, never the model's).
    `urls` are STRUCTURED hit URLs carried on the deps, never re-parsed
    from display-formatted evidence (a hostile page title could swallow
    or forge pool entries there)."""
    pool = {canonical_url(u): u for u in _POOL_URL.findall(prompt)}
    pool.update({canonical_url(u): u for u in urls})
    return pool


def ground_value(value: str, allowed: dict[str, str]) -> str:
    """Every URL in the value grounds or goes: a whole-value URL
    rewrites to its source form or blanks the value; a URL embedded in
    prose rewrites in place or is excised (the prose survives, the
    fabrication does not)."""
    stripped = value.strip()
    if _TEXT_URL.fullmatch(stripped):
        return allowed.get(canonical_url(stripped), "")

    def replace(match: re.Match) -> str:
        return allowed.get(canonical_url(match.group(0)), "")

    grounded = _TEXT_URL.sub(replace, value)
    # An excised bracketed URL must not leave "[]" litter behind.
    grounded = re.sub(r"\[\s*\]", "", grounded)
    return re.sub(r"[ \t]{2,}", " ", grounded).strip()
