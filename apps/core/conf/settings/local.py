import os

# Seed local-only defaults BEFORE importing base.py, so its `os.environ[...]`
# reads resolve to localhost values when the env didn't set them. Keeps
# base.py honest (no localhost fallbacks baked into prod settings) while
# the dev loop still works out of the box.
os.environ.setdefault("BASE_URL", "http://localhost:8002")
# The web app dev server (web/apps/app runs `next dev -p 3003`).
os.environ.setdefault("APP_BASE_URL", "http://localhost:3003")
# The local identity service (the hosted repo serves it on :8001).
os.environ.setdefault("OPENBOWER_AUTH_URL", "http://localhost:8001")
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
# connections in dev; hosted gunicorn keeps the base default (60).
os.environ.setdefault("DB_CONN_MAX_AGE", "0")

from common.env import env_bool

from .base import *  # noqa: F403

DEBUG = env_bool("DEBUG", "true")
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# CORS: explicit allowlist even in dev. Allow-all + credentials would let
# any localhost page credentialed-fetch /v1/auth/me and read session-bearing
# responses.
CORS_ALLOWED_ORIGINS = [os.environ["APP_BASE_URL"]]
CSRF_TRUSTED_ORIGINS = [os.environ["APP_BASE_URL"]]
