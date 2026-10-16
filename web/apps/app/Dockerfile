# Production image for the product app. Build context is the web/
# workspace (the single pnpm lock and every member manifest live there);
# the dev shell compose mounts the source into is web/Dockerfile.
#
# NEXT_PUBLIC_* values are inlined into the browser bundle by `next
# build`, so they are build arguments, and they have NO default: the app
# refuses to serve a page in production without them, and a default
# here would bypass that refusal. The publishing workflow and the PR
# build pass the single-machine install's values (the backend on
# localhost:8002, the hosted identity provider); an install on its own
# domain builds with its own. API_INTERNAL_URL is read at runtime and a
# container run sets it (compose: http://core:8002), since inside the
# container localhost is the container itself.

# ---------- deps ----------
FROM node:24-alpine AS deps
WORKDIR /web
ENV PNPM_HOME=/pnpm
# The workspace manifest is the one home for the pnpm version: corepack
# reads packageManager from it. Then every package the lock names is
# downloaded from the lock alone, so no member manifest is copied here
# and adding a workspace package needs no line in this file. The store
# is a build cache mount, so even a lockfile change re-downloads only
# what is new; the layer itself is keyed on the lock.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    corepack enable && corepack prepare --activate \
    && pnpm config set store-dir /pnpm/store \
    && pnpm fetch

# ---------- build ----------
FROM deps AS build
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_AUTH_URL
# The sign-in providers to offer; none unless the build says so, since
# only an identity service holding provider credentials can honor them.
ARG NEXT_PUBLIC_AUTH_PROVIDERS=""
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_AUTH_URL=$NEXT_PUBLIC_AUTH_URL \
    NEXT_PUBLIC_AUTH_PROVIDERS=$NEXT_PUBLIC_AUTH_PROVIDERS \
    NEXT_TELEMETRY_DISABLED=1
COPY . .
# Offline: everything was fetched above, this only links the app and
# its workspace packages (the marketing site is left out by the filter).
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --offline --frozen-lockfile --filter @bower/app... \
    && pnpm --filter @bower/app exec next build

# ---------- runtime ----------
FROM node:24-alpine
WORKDIR /web
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3003
# The standalone output keeps the workspace layout, so the server lives
# at apps/app/server.js and expects its static assets beside it. The
# tree stays root-owned and read-only to the runtime user; the server
# writes only its cache, so that directory alone is handed over.
COPY --from=build /web/apps/app/.next/standalone ./
COPY --from=build /web/apps/app/.next/static ./apps/app/.next/static
RUN mkdir -p apps/app/.next/cache && chown 1000:1000 apps/app/.next/cache
# Numeric, the same uid the core image runs as, so a bind-mounted file
# one can read the other can too and a runAsNonRoot policy can check it.
USER 1000
EXPOSE 3003
CMD ["node", "apps/app/server.js"]
