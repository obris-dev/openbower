import os

# Seed local-only defaults BEFORE importing base.py, so its `os.environ[...]`
# reads resolve to localhost values when the env didn't set them. Keeps
# base.py honest (no localhost fallbacks baked into prod settings) while
# the dev loop still works out of the box.
os.environ.setdefault("BASE_URL", "http://localhost:8002")
# The web app dev server (web/apps/app runs `next dev -p 3003`).
os.environ.setdefault("APP_BASE_URL", "http://localhost:3003")
# The local identity service (:8001 by suite convention).
os.environ.setdefault("OPENBOWER_AUTH_URL", "http://localhost:8001")
os.environ.setdefault("OPENBOWER_DATA_URL", "http://localhost:8003")
# The zero-config inference door (see the ollama source below). A
# containerized app reaches the host's Ollama via host.docker.internal,
# not localhost, so compose overrides this.
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434/v1")
# Browser to Django is plain HTTP in dev; secure cookies would never get sent.
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")
# Dev Postgres: the docker-compose db service, mapped to host port 5433.
# Dev creds only; base.py requires the password from env so a real deploy
# can never fall back to these.
os.environ.setdefault("POSTGRES_DB", "openbower")
os.environ.setdefault("POSTGRES_USER", "openbower")
os.environ.setdefault("POSTGRES_PASSWORD", "openbower")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5433")
# Dev servers spawn a thread per request and Django's persistent
# connections outlive dead threads, so CONN_MAX_AGE>0 under runserver LEAKS
# one Postgres connection per request until max_connections. Ephemeral
# connections in dev; a deployed gunicorn keeps the base default (60).
os.environ.setdefault("DB_CONN_MAX_AGE", "0")

from django.core.exceptions import ImproperlyConfigured

from openbower_kernel.env import env_bool
from openbower_kernel.provider_config import ProviderSpec, make_source

from .base import *  # noqa: F403

DEBUG = env_bool("DEBUG", "true")
# "core" is this service's docker-compose name: in the containerized local
# stack, sibling containers (the web dev server's SSR fetches) reach it by
# that name, and Django rejects any Host it isn't told about.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "core"]

# The zero-config dev loop: a natively-run Ollama through the
# OpenAI-compatible door, added only when the operator's config file
# didn't already name an ollama source. Keyless is fine (non-canonical
# bases are open); absent Ollama degrades to an empty catalog honestly.
#
# make_source does no validation, unlike the config-file path (which
# refuses a schemeless base loudly), so an operator-supplied base is
# checked here: a typo would otherwise read as a non-canonical (open)
# source and fail per row at HTTP time instead of refusing at startup.
_OLLAMA_BASE_URL = os.environ["OLLAMA_BASE_URL"]
if not _OLLAMA_BASE_URL.startswith(("http://", "https://")):
    raise ImproperlyConfigured(f"OLLAMA_BASE_URL must start with http:// or https://, got {_OLLAMA_BASE_URL!r}")
OPENAI_COMPATIBLE_SOURCES.setdefault(  # noqa: F405
    "ollama",
    make_source(ProviderSpec.OPENAI_COMPATIBLE.value, base_url=_OLLAMA_BASE_URL, concurrency=1),
)

# CORS: explicit allowlist even in dev. Allow-all + credentials would let
# any localhost page credentialed-fetch /v1/auth/me and read session-bearing
# responses.
CORS_ALLOWED_ORIGINS = [os.environ["APP_BASE_URL"]]
CSRF_TRUSTED_ORIGINS = [os.environ["APP_BASE_URL"]]
