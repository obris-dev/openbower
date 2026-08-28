# Dev image for the web monorepo. Bakes pnpm in at build time so
# `docker compose up` doesn't re-download it on every container start.
# Dependency install still happens in the entrypoint command in
# docker-compose.yml because the node_modules volume is mounted at
# start, hiding anything baked into the image.

FROM node:24-alpine

WORKDIR /web

# The workspace manifest is the ONE home for the pnpm version: corepack
# reads `packageManager` from it, so the pin cannot drift from a second
# copy here. Manifest only, so this layer survives every source edit.
COPY package.json ./
RUN corepack enable && corepack prepare --activate
