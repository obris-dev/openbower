"""Test settings: local dev config, but hermetic.

`DJANGO_ENV=test manage.py test` selects this. In-memory cache so the OAuth
state store needs no db cache table and never leaks across tests.
"""

from .local import *  # noqa: F403

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
