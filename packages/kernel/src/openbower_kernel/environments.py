"""The deployment environment selector.

DJANGO_ENV picks the settings module (`conf.settings.<env>`) and is read by
the wsgi / asgi / manage entry points via `current_env()`. It is ALWAYS
explicit: unset refuses to start with a one-line error naming the choices,
so `cloud` only ever runs because a deploy wrote it down and `local` never
runs anywhere by accident. A set-but-invalid value raises naming the bad
value rather than a later opaque ModuleNotFoundError for
`conf.settings.<typo>`.

Dependency-free (no Django access) so the entry points can import it before
DJANGO_SETTINGS_MODULE is resolved.
"""

from __future__ import annotations

import os
from enum import StrEnum


class DjangoEnv(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"
    TEST = "test"


_CHOICES = ", ".join(e.value for e in DjangoEnv)


def current_env() -> DjangoEnv:
    """Resolve DJANGO_ENV to a DjangoEnv member. Unset exits with a
    teaching message (set `local` to run on this machine, `cloud` for a
    deployed instance); invalid raises ValueError naming the value."""
    raw = os.environ.get("DJANGO_ENV")
    if not raw:
        raise SystemExit(f"DJANGO_ENV is not set. Set DJANGO_ENV=local to run on this machine, or one of: {_CHOICES}.")
    try:
        return DjangoEnv(raw)
    except ValueError:
        raise SystemExit(f"DJANGO_ENV={raw!r} is not a known environment; choose one of: {_CHOICES}.") from None
