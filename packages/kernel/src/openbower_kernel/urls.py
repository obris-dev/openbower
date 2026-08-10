"""URL helpers shared across `/v1/` app urlconfs.

`api_path` is `django.urls.path` that matches with OR without a trailing
slash. We keep all API routes canonically without the slash; this helper
just makes `/foo` and `/foo/` both land on the same view so curlers,
clients, and humans don't trip on a 404 when they happen to type one.

Django's `APPEND_SLASH = True` only helps for GETs (it issues a 301 that
most non-GET clients don't follow). The proper fix is to make both
forms match in URL resolution, which is what this helper does.
"""

from __future__ import annotations

import re
from typing import Any

from django.urls import include, re_path

# `path()`-style converters. Any pattern using a converter outside this
# set raises a clear error in `_convert_to_regex` (add it here).
#
# No `path` (`.+`) converter on purpose: `.+` is greedy, so the
# trailing `/?$` this helper appends would capture the slash INTO the
# value instead of making it optional, silently defeating the helper.
# The converters below all exclude `/`, so the optional-slash logic is
# correct for them. No route needs `<path:>`; if one ever does, add it
# with slash-correct handling and a test, not a bare `.+`.
_CONVERTER_REGEX: dict[str, str] = {
    "str": r"[^/]+",
    "int": r"\d+",
    "slug": r"[-a-zA-Z0-9_]+",
    "uuid": r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}",
}

_CONVERTER_RE = re.compile(r"<(?:(\w+):)?(\w+)>")


def _convert_to_regex(match: re.Match[str]) -> str:
    converter = match.group(1) or "str"
    name = match.group(2)
    pattern = _CONVERTER_REGEX.get(converter)
    if pattern is None:
        raise ValueError(
            f"api_path() doesn't know the {converter!r} converter; add it to _CONVERTER_REGEX in openbower_kernel/urls.py."
        )
    return f"(?P<{name}>{pattern})"


def api_path(route: str, view: Any, *, name: str | None = None):
    """`path()`-style URL that matches with or without trailing slash.

    Accepts the same converter syntax as `django.urls.path` for the
    types listed in `_CONVERTER_REGEX`. Translates the route into a
    regex with an optional trailing slash and wires it as `re_path`.
    """
    pattern = _CONVERTER_RE.sub(_convert_to_regex, route)
    # Empty route = the include's root. The parent include() handles
    # trailing-slash optionality on the prefix (via re_path), so we
    # just need an exact-empty match here. Generating `^/?$` would
    # trip Django's W002 ("leading slash in pattern").
    trailing = "" if pattern == "" else "/?"
    return re_path(rf"^{pattern}{trailing}$", view, name=name)


def api_include(prefix: str, module: str):
    """`path(..., include(...))` with optional trailing slash on the prefix.

    Pair with `api_path` inside the included urlconf for end-to-end
    trailing-slash-optional routing. The prefix should NOT include a
    trailing slash; the helper appends `/?` so both forms match.

    The `/?` is deliberately reverse-friendly (a bounded `(?:/|$)` would
    break `reverse()` on the included names). A consequence is that
    `/{prefix}foo` aliases `/{prefix}/foo` onto the same view; harmless, since
    it is the same resolved view and adds no route.
    """
    return re_path(rf"^{prefix}/?", include(module))
