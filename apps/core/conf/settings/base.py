import logging
import os
import time
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from openbower_kernel.env import env_bool, env_list
from openbower_kernel.provider_config import ProviderConfigError, ProviderSpec, resolve_provider_sources

# Log timestamps in UTC regardless of the host clock. Python's logging
# `asctime` uses `time.localtime` by default; the app is TIME_ZONE="UTC",
# so force the formatter converter to match (and the datefmt carries a
# trailing `Z` to say so explicitly).
logging.Formatter.converter = time.gmtime

BASE_DIR = Path(__file__).resolve().parents[2]  # core/
REPO_ROOT = BASE_DIR.parents[1]  # repo root (apps/core -> apps -> root)

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# The environment (local / cloud / test) is selected by DJANGO_ENV at the
# wsgi/asgi/manage entry points (see conf.environments), which pick the
# settings module; this module IS the selected env's base, so it doesn't
# re-read DJANGO_ENV. Per-env overrides live in local.py / cloud.py / test.py.
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "rest_framework",
]

# Single source of truth for our apps: drives INSTALLED_APPS and the
# per-app logger config below. Add an app here once and it gets a logger
# automatically, no second list to keep in sync.
#
LOCAL_APPS = [
    "common",
    "auth_client",
    "discover",
    "lists",
    "agents",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CORS must come before CommonMiddleware so preflight responses get the
    # right headers regardless of other middleware short-circuiting.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "conf.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "conf.wsgi.application"

# Postgres, always (mirrors the suite convention). Params are env-driven;
# conf.settings.local seeds the docker-compose dev defaults. The password has
# no default here: a missing password must fail loudly, not silently fall
# back to a known credential (same rule as SECRET_KEY).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "openbower"),
        "USER": os.environ.get("POSTGRES_USER", "openbower"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Base URL of THIS backend (absolute URLs, the OAuth redirect_uri).
# Required; local.py / cloud.py supply env-specific defaults.
BASE_URL = os.environ["BASE_URL"].rstrip("/")

# Where the product WEB APP lives (the post-login redirect target, and the
# cookie-credentialed CORS origin). Required; local.py / cloud.py supply
# env-specific defaults.
APP_BASE_URL = os.environ["APP_BASE_URL"].rstrip("/")

# The identity provider (openbower-auth). The app is an OAuth client of it;
# it holds no account authority of its own. Required; local.py / cloud.py
# supply env-specific defaults.
OPENBOWER_AUTH_URL = os.environ["OPENBOWER_AUTH_URL"].rstrip("/")

# The public OAuth client id registered at the IdP (bootstrap_oauth_app
# pins "openbower-app" by default). Not a secret: PKCE + exact redirect
# URIs protect the flow.
OAUTH_CLIENT_ID = os.environ.get("OAUTH_CLIENT_ID", "openbower-app")

# Server-to-server transport to the IdP. Inside a container, localhost is
# the container, so the network path this process uses can differ from the
# browser's. OPENBOWER_AUTH_URL stays the service's IDENTITY everywhere it
# names the service (resource indicators, browser navigation); this base
# carries only the HTTP calls this process makes itself (token exchange,
# revocation, /me). Defaults to the canonical URL, so host runs set nothing.
# `or`, not get(default): a set-but-empty `NAME=` must fall back, not
# become a relative URL that fails every call to this service.
OPENBOWER_AUTH_INTERNAL_URL = (os.environ.get("OPENBOWER_AUTH_INTERNAL_URL") or OPENBOWER_AUTH_URL).rstrip("/")

# The IdP's OWN API version prefix, for the cross-service /me call. Separate
# from this app's API_VERSION_PREFIX so bumping ours never repoints it.
OPENBOWER_AUTH_API_VERSION = os.environ.get("OPENBOWER_AUTH_API_VERSION", "v1")

# The data service (company universe + look-alike index). The app calls it
# server-to-server on the user's behalf, forwarding the session's IdP
# access token (which carries data:read). Same settings trio as the IdP
# client above. Required; local.py / cloud.py supply env defaults.
OPENBOWER_DATA_URL = os.environ["OPENBOWER_DATA_URL"].rstrip("/")
# Same identity/transport split as the IdP above: every data-service call is
# server-to-server, so transport crosses this base while OPENBOWER_DATA_URL
# stays the audience the token names. Defaults to the canonical URL (`or`,
# not get(default), for the same set-but-empty reason as the IdP base).
OPENBOWER_DATA_INTERNAL_URL = (os.environ.get("OPENBOWER_DATA_INTERNAL_URL") or OPENBOWER_DATA_URL).rstrip("/")
OPENBOWER_DATA_API_VERSION = os.environ.get("OPENBOWER_DATA_API_VERSION", "v1")
DATA_HTTP_TIMEOUT_SECONDS = int(os.environ.get("DATA_HTTP_TIMEOUT_SECONDS", "10"))


# RFC 8707 resource indicators for the session access token. The token is
# used at TWO resource servers, so it names both as its audience: the IdP
# itself (its /v1/auth/me userinfo, which the toolkit audience-checks) and
# the data service (which enforces its own audience on introspection). Each
# audience is that service's real base URL, so it matches per env (dev
# localhost, cloud openbower.com); binding to only one would get the token
# rejected at the other.
OAUTH_RESOURCES = [OPENBOWER_AUTH_URL, OPENBOWER_DATA_URL]

# Inference providers for agents: two API SPECS, each holding NAMED SOURCES
# so one deploy can run several servers of the same spec side by side
# (a local Ollama AND the canonical vendor). The config file
# (config/providers.toml, operator-owned, gitignored; template in
# config/templates/) is the ONE custody: structure AND keys, inline,
# the aws-credentials norm. No env-key mirror and no no-file defaults
# (two custodies for one fact was two places for it to drift); with
# no file, only local.py's keyless ollama seed exists for the dev
# loop. A source is OPEN when its key is set or its base is
# non-canonical; a keyless canonical source is closed.
# `or`, not get(default): PROVIDERS_CONFIG= (set but empty, the
# uncommented .env.example line) must fall back, not become Path(".").
_PROVIDERS_CONFIG_PATH = Path(os.environ.get("PROVIDERS_CONFIG") or REPO_ROOT / "config" / "providers.toml")
try:
    _SOURCES = resolve_provider_sources(_PROVIDERS_CONFIG_PATH)
except ProviderConfigError as e:
    # Django's own boot-failure shape: startup machinery prints it as
    # configuration, not a stack of kernel internals.
    raise ImproperlyConfigured(str(e)) from e
OPENAI_COMPATIBLE_SOURCES = _SOURCES[ProviderSpec.OPENAI_COMPATIBLE]
ANTHROPIC_COMPATIBLE_SOURCES = _SOURCES[ProviderSpec.ANTHROPIC_COMPATIBLE]

# The search seam behind agents' evidence tools, two providers:
# DuckDuckGo by DEFAULT: free and keyless, so web search works out of
# the box and offloads the paid provider. Contact search PINS DataForSEO
# regardless (LinkedIn x-rays need Google-grade SERPs) and stays gated
# until its credentials are set.
SEARCH_PROVIDER = os.environ.get("SEARCH_PROVIDER", "duckduckgo")
# The provider names, mirrored from agents.constants.SearchProvider
# (settings cannot import app code; a parity test pins the mirror). A
# typo'd provider is a CONFIG error and refuses at startup, distinct
# from missing credentials (which gate honestly at runtime).
_SEARCH_PROVIDER_CHOICES = ("duckduckgo", "dataforseo")
if SEARCH_PROVIDER not in _SEARCH_PROVIDER_CHOICES:
    raise ImproperlyConfigured(f"SEARCH_PROVIDER must be one of {_SEARCH_PROVIDER_CHOICES}, not {SEARCH_PROVIDER!r}")
DATAFORSEO_LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
DATAFORSEO_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")

# Timeout (seconds) for every server-to-IdP HTTP call (token exchange,
# /me, refresh, revoke), so a hung IdP can't pin a worker.
AUTH_HTTP_TIMEOUT_SECONDS = int(os.environ.get("AUTH_HTTP_TIMEOUT_SECONDS", "10"))

# Early-refresh buffer (seconds): the session rotates the access token this
# long BEFORE its real expiry, so a token handed to a downstream resource
# server (introspected there a beat later) still has comfortable life and does
# not lapse in-flight. Keep it well under the access-token lifetime (otherwise
# every request would look due for refresh).
AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = int(os.environ.get("AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS", "30"))

# Cooldown (seconds) after a transient refresh failure: the session's access
# expiry is pushed out to now + skew + cooldown, so for `cooldown` seconds
# requests take the fast path during a brief IdP outage instead of re-attempting
# the refresh every request (the skew term cancels in the fast-path check, so
# this holds regardless of skew's relation to cooldown). Keep well under the
# access-token lifetime.
AUTH_REFRESH_COOLDOWN_SECONDS = int(os.environ.get("AUTH_REFRESH_COOLDOWN_SECONDS", "30"))

# Both timing knobs must be at least 1 second: a 0 skew reopens the in-flight
# expiry race, and a 0 cooldown gives an IdP outage no fast-path window, so
# every request re-attempts the refresh and thundering-herds a recovering IdP.
# Fail fast on a misconfig rather than degrade silently.
if AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS < 1 or AUTH_REFRESH_COOLDOWN_SECONDS < 1:
    raise ImproperlyConfigured(
        "AUTH_ACCESS_TOKEN_REFRESH_SKEW_SECONDS and AUTH_REFRESH_COOLDOWN_SECONDS must both be >= 1"
    )

# Age (days since revocation) past which `manage.py prune_sessions` deletes a
# revoked AppSession row.
APP_SESSION_PRUNE_DAYS = int(os.environ.get("APP_SESSION_PRUNE_DAYS", "30"))

# Fernet key for encrypting the IdP tokens stored on AppSession (see
# auth_client.fields.EncryptedTextField). Falls back to SECRET_KEY so dev needs
# no extra config; set a dedicated secret in cloud so token ciphertext does
# not share a fate with the signing key. Rotating it makes existing
# ciphertext undecryptable (those sessions just re-login), so rotate
# deliberately.
AUTH_TOKEN_ENCRYPTION_KEY = os.environ.get("AUTH_TOKEN_ENCRYPTION_KEY", "")

# `bwr_session` browser cookie (set by auth_client.cookies). Secure by
# default in base; local.py loosens it for plain-HTTP dev.
AUTH_COOKIE_SECURE = env_bool("AUTH_COOKIE_SECURE", "true")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN", "")
AUTH_COOKIE_MAX_AGE_SECONDS = int(os.environ.get("AUTH_COOKIE_MAX_AGE_SECONDS", str(30 * 86400)))

# All HTTP API routes are mounted under /{API_VERSION_PREFIX}/. The web
# frontend reads the same value; keep them in lockstep when bumping.
API_VERSION_PREFIX = os.environ.get("API_VERSION_PREFIX", "v1")

# Product version: version.txt at the repo root. Exposed on /healthz.
# Best-effort (never crash settings import over a cosmetic string).
try:
    PRODUCT_VERSION = (BASE_DIR / "version.txt").read_text(encoding="utf-8").strip() or "unknown"
except (OSError, UnicodeDecodeError):
    PRODUCT_VERSION = "unknown"

# The needs-attention follow-up user-facing messages compose (the
# crash handler among them, INSIDE its except block: a missing name
# here would defeat the never-stuck-pending promise itself). Profiles
# override with what they can stand behind; this default is safe
# anywhere. The BOUND exists because the follow-up rides inside
# bounded wire fields (a crashed run's 256-char error): overlong
# operator config refuses at boot instead of truncating mid-URL.
SUPPORT_FOLLOWUP_MAX_LENGTH = 128
SUPPORT_FOLLOWUP = "check the API server's logs"

# CORS / CSRF for the web client. Empty by default so prod has to opt in
# explicitly via env; `local.py` overrides with the dev web origin.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

REST_FRAMEWORK = {
    # The app session cookie (opaque id -> server-side record holding the
    # user's IdP tokens) is the only credential on this API in Phase 1.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "auth_client.authentication.AppSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    # Maps auth failures onto the stable {"error", "detail"} shape.
    "EXCEPTION_HANDLER": "auth_client.exception_handlers.auth_exception_handler",
}

# Cache. Django's db cache: zero deps, survives process restart. Holds the
# short-lived OAuth state -> PKCE-verifier bags during the login redirect.
# Run `manage.py createcachetable` once on a cold db (the make targets do).
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "django.core.cache.backends.db.DatabaseCache")
CACHE_LOCATION = os.environ.get("CACHE_LOCATION", "openbower_cache")
# MAX_ENTRIES is raised well above DatabaseCache's default 300 because this
# cache holds pending login state bags: culling at 300 would evict states
# mid-handshake under a burst and fail legitimate logins with
# state_mismatch. Matches the identity service's setting.
CACHE_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "10000"))
CACHES = {
    "default": {"BACKEND": CACHE_BACKEND, "LOCATION": CACHE_LOCATION, "OPTIONS": {"MAX_ENTRIES": CACHE_MAX_ENTRIES}},
}

# App loggers are configured explicitly so warnings surface predictably
# regardless of Python's default lastResort handler quirks. App level is INFO
# by default; per-logger override via `LOG_LEVEL_<APP>` env so an operator can
# crank one app to DEBUG without editing code. Django's own loggers are left
# at framework defaults via `disable_existing_loggers=False`.

LOG_LEVEL_APP = os.environ.get("LOG_LEVEL_APP", "INFO").upper()


def _app_log_level(app: str) -> str:
    key = f"LOG_LEVEL_{app.upper()}"
    return os.environ.get(key, LOG_LEVEL_APP).upper()


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {
            "format": "{asctime} {levelname:<7} {name}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%SZ",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "concise",
            # stdout so application logs interleave cleanly with
            # management-command output and `docker compose logs`.
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        app: {
            "handlers": ["console"],
            "level": _app_log_level(app),
            "propagate": False,
        }
        for app in LOCAL_APPS
    },
}
