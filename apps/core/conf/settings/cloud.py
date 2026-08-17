"""Cloud (hosted) settings. `DJANGO_ENV=cloud` -> `conf.settings.cloud`.

The production counterpart to `local.py`. Extends `base.py` with
production-safe values and the canonical openbower.com URLs, so a deploy
supplies only secrets and infra hosts, not a wall of config.

DEPLOY CHECKLIST (set these in the cloud env; everything else has a sane
default here or in base.py). (S) = secret, store in the secret manager.

    DJANGO_ENV=cloud             selects this settings module
    DJANGO_SECRET_KEY       (S)  long random string
    POSTGRES_HOST                managed db host (DB/USER/PORT default to openbower/openbower/5432)
    POSTGRES_PASSWORD       (S)  db password
    AUTH_TOKEN_ENCRYPTION_KEY (S, recommended)  dedicated secret for AppSession
                                 token encryption; falls back to DJANGO_SECRET_KEY
                                 if unset (rotating either re-logs sessions in)

Defaulted to the openbower.com deployment (override via env for another):
    BASE_URL, APP_BASE_URL, OPENBOWER_AUTH_URL, OPENBOWER_DATA_URL, ALLOWED_HOSTS,
    CORS_ALLOWED_ORIGINS, CSRF_TRUSTED_ORIGINS.
This instance's own IP is auto-added to ALLOWED_HOSTS at runtime for health
checks.

One-time / cold-db step: run `manage.py createcachetable`.
"""

import contextlib
import os
import socket

# Canonical openbower.com URLs, seeded BEFORE base.py reads them as required
# os.environ[...] (the same pattern local.py uses for its localhost
# defaults). Override any via env for a different deployment.
os.environ.setdefault("BASE_URL", "https://api.openbower.com")
os.environ.setdefault("APP_BASE_URL", "https://app.openbower.com")
os.environ.setdefault("OPENBOWER_AUTH_URL", "https://auth.openbower.com")
os.environ.setdefault("OPENBOWER_DATA_URL", "https://data.openbower.com")
# Parent-domain cookie scope: the web app's middleware and server-side
# guard read bwr_session on the APP origin, so a host-only cookie on the
# api host would silently disable them (and loop login).
os.environ.setdefault("AUTH_COOKIE_DOMAIN", ".openbower.com")
os.environ.setdefault("ALLOWED_HOSTS", "api.openbower.com")
_ORIGINS = "https://app.openbower.com"
os.environ.setdefault("CORS_ALLOWED_ORIGINS", _ORIGINS)
os.environ.setdefault("CSRF_TRUSTED_ORIGINS", _ORIGINS)

from openbower_kernel.env import env_bool, split_csv  # noqa: E402

from .base import *  # noqa: E402, F403

# Never debug in the hosted env.
DEBUG = False

# Host(s) this serves (seeded above; override via env).
ALLOWED_HOSTS = split_csv(os.environ["ALLOWED_HOSTS"])

# Allow this instance's own IP so health checks that reach it directly by IP
# (rather than by hostname) aren't rejected by ALLOWED_HOSTS under
# DEBUG=False. Resolved at runtime; suppress(gaierror) so a resolution miss
# never crashes boot.
with contextlib.suppress(socket.gaierror):
    ALLOWED_HOSTS.append(socket.gethostbyname(socket.gethostname()))

# Transport security. Behind a TLS-terminating proxy / load balancer: trust
# its forwarded scheme so Django knows the original request was HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# HTTP to HTTPS redirect ON by default: with SECURE_PROXY_SSL_HEADER above, a
# standard TLS-terminating proxy makes this safe, and it's fully recoverable
# (set SECURE_SSL_REDIRECT=false) if a no-TLS bring-up needs it off.
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", "true")
# HSTS stays opt-in (default 0); a wrong default can lock the domain to
# HTTPS for the whole max-age. Turn it on once TLS is confirmed stable.
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", "false")
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", "false")
