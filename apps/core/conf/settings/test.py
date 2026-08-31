"""Test settings: local dev config, but hermetic.

`DJANGO_ENV=test manage.py test` selects this. In-memory cache so the OAuth
state store needs no db cache table and never leaks across tests.
"""

from .local import *  # noqa: F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Hermetic inference + search: local.py seeds the dev loop's live doors
# (a running Ollama; DuckDuckGo needs no config); tests must never
# reach a real server, so
# every door is pinned shut and each test opens what it mocks.
OPENAI_COMPATIBLE_SOURCES = {}
ANTHROPIC_COMPATIBLE_SOURCES = {}
# Deliberately NOT a valid door: any test that reaches the search seam
# without patching it gets a not_configured answer instead of making a
# live network call. Tests that want a door set one explicitly.
SEARCH_PROVIDER = ""
# The dev .env may hold REAL DataForSEO credentials; contacts gate on
# them regardless of the provider switch, so they must be pinned shut
# too or the suite reads the operator's paid door as available.
DATAFORSEO_LOGIN = ""
DATAFORSEO_PASSWORD = ""
