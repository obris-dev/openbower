"""Test settings: local dev config, but hermetic.

`DJANGO_ENV=test manage.py test` selects this. In-memory cache so the OAuth
state store needs no db cache table and never leaks across tests.
"""

from .local import *  # noqa: F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Hermetic inference + search: local.py seeds the dev loop's live providers
# (a running Ollama; DuckDuckGo needs no config); tests must never
# reach a real server, so
# every provider is pinned shut and each test opens what it mocks.
INFERENCE_SOURCES = {}
# Hermetic tools: wiring points every tool at a registered vendor
# whose credentials are pinned absent, so the seam answers
# not_configured instead of reaching a live engine (the operator's
# real config/tools.toml may hold live keys); each test opens what
# it mocks.
TOOL_VENDOR_KEYS = {}
TOOL_WIRING = {"web_search": "dataforseo", "find_contacts": "dataforseo"}
