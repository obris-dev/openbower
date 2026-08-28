# uv workspace image for the `openbower-core` member.
#
# Build context is the REPO ROOT (see docker-compose.yml): the single uv.lock +
# every member's pyproject.toml live there and uv resolves the whole workspace.
#
# Multi-stage: psycopg[c] (the cloud group) compiles against libpq, so the
# builder carries the compiler + libpq headers; the runtime ships only the
# libpq5 shared lib and runs non-root, staying slim.

# ---------- builder ----------
FROM python:3.14-slim AS builder

# Pinned: an unpinned uv can change lockfile expectations under a
# `--frozen` sync with no change in this repo, and the two stages must
# resolve to the same version. Bump both together.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

# PYTHONUNBUFFERED: real-time management-command output when piped.
# UV_LINK_MODE=copy: avoid hardlink warnings across the cache mount.
# UV_PYTHON_*: use the base image's system Python (3.14), never download a
# uv-managed interpreter. The managed one lands under /root (mode 700) and
# breaks the non-root runtime; pinning the base to the same version keeps
# the venv on the world-readable /usr/local interpreter.
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

# Build deps for psycopg[c] (compiled against libpq). Builder-only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Deps-only layer: just the manifests needed to resolve the workspace, so this
# cache layer only busts when dependencies change. It selects the SAME sets as
# the member sync below (--no-dev drops the default dev group; --group cloud
# adds gunicorn and psycopg[c]), so the libpq compile lands here rather than on
# the post-COPY layer that busts on every source edit.
# One COPY per workspace member: adding a member means adding a line.
COPY pyproject.toml uv.lock ./
COPY apps/core/pyproject.toml apps/core/pyproject.toml
COPY packages/kernel/pyproject.toml packages/kernel/pyproject.toml
COPY packages/openbower-schema/pyproject.toml packages/openbower-schema/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package openbower-core --group cloud

# Full workspace, then install just this member (+ its workspace deps, the
# kernel and schema packages). --no-dev keeps prod lean (no ruff/psycopg
# [binary]); --group cloud adds gunicorn + psycopg[c] (compiled here against
# libpq).
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package openbower-core --group cloud

# ---------- runtime ----------
FROM python:3.14-slim

# Pinned: an unpinned uv can change lockfile expectations under a
# `--frozen` sync with no change in this repo, and the two stages must
# resolve to the same version. Bump both together.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system

# Runtime shared lib for the compiled psycopg[c].
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The built venv + app code, owned by the non-root runtime user (uid 1000). The
# --chown is what lets the same non-root user re-sync the venv in the dev
# compose flow (`uv run` without --no-sync); prod uses --no-sync and only reads
# it. The venv's python symlinks to /usr/local/bin's 3.14 (the runtime base).
COPY --from=builder --chown=1000:1000 /app /app

# Drop root (defense-in-depth). App code and .venv are world-readable, and the
# prod CMD's --no-sync never touches the environment, so THAT path runs under a
# read-only root fs; the dev compose command deliberately re-syncs into .venv,
# which is what the --chown above is for.
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8002
# Prod default: gunicorn. `--chdir apps/core` puts apps/core on the path so
# `conf.wsgi` (and `conf.settings`, app packages) import the same way manage.py
# arranges them. DJANGO_ENV has no default here on purpose: unset refuses to
# start, so the deploy environment must say which profile it is. Dev overrides
# this command with runserver via the docker-compose `command:`.
CMD ["uv", "run", "--no-sync", "--no-dev", "--package", "openbower-core", "--group", "cloud", \
     "gunicorn", "--chdir", "apps/core", "conf.wsgi:application", \
     "--bind", "0.0.0.0:8002", "--workers", "3", "--timeout", "120"]
