"""The deployment environment selector.

DJANGO_ENV picks the settings module (`conf.settings.<env>`) and is read by
the wsgi / asgi / manage entry points via `current_env()`. Unset defaults to
CLOUD, the locked-down hosted env, so a deploy that forgets to set it fails
safe rather than into permissive dev settings.

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


DEFAULT_ENV = DjangoEnv.CLOUD


def current_env() -> DjangoEnv:
    """Resolve DJANGO_ENV to a DjangoEnv member, defaulting to DEFAULT_ENV when
    unset. A set-but-invalid value raises ValueError naming the bad value,
    rather than a later opaque ModuleNotFoundError for `conf.settings.<typo>`."""
    raw = os.environ.get("DJANGO_ENV")
    return DjangoEnv(raw) if raw else DEFAULT_ENV
