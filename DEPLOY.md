# Deploying OpenBower

The self-host footprint: what `docker-compose.yml` runs, where to look when something is off, and the environment facts a deploy needs. The [README](README.md) covers the quickstart and the sign-in prerequisite.

## The services

A working deploy is the services below plus a one-shot migrator; `docker-compose.yml` carries all of them.

| Service | What it runs | Where to look |
|---------|--------------|---------------|
| db      | Postgres 16 (host port 5433) | `make local-dbshell` for psql |
| core-setup | migrations and the cache table, once per start; core, the workers, and the cron wait for it to finish | `make logs` |
| core    | the Django api on :8002 | `make logs-core` |
| jobs    | the background jobs loop (`manage.py run_jobs`): a fill's walk over its sheet, the webhook backfill, the rank re-space | `make logs` |
| fill-provisioner, fill-consumer | the manual fill lane: the provisioner publishes an open fill's queued runs to the bus, the consumer claims each, runs the research agent, and lands the cell | `make logs` |
| autofill-provisioner, autofill-consumer | the same two roles for automatic runs (a pushed row's AI columns) | `make logs` |
| preview-consumer | the builder's Test on its own lane: the autofill provisioner routes a preview run here, so a watched one-row diagnostic never waits behind a fill or a pushed-row burst | `make logs-autofill` |
| ingest-worker | the webhook ingest consumer | `make logs` |
| cron    | supercronic over `apps/core/crontab`: scheduled maintenance (the hourly prune of preview runs older than a day, the stale-run reclaim, the deferred flush and delivery prune) | `make logs-cron` |
| web     | the Next.js apps: the product app on :3003, the marketing site on :3004 | `make logs-web` |

The database and the web dependencies survive in named volumes; the Python venv is an anonymous volume the next start re-syncs (`make prune-venvs` clears the strays). SIGTERM or SIGINT lets a consumer's rows in flight finish before it exits, so restarting one is always safe and can take a while. `make db-up` starts just the database, which is what the host-run test suite needs.

Environment facts (`apps/core/.env.example` carries the full annotated list):

- `DJANGO_ENV` is always explicit (`local` for a machine you own, `cloud` for a deployed instance; unset refuses to start). The api and the workers read the same settings profile, so they see the same providers and search configuration.
- Repointing the app at a different identity provider means setting both halves: `OPENBOWER_AUTH_URL`, the identity (the browser's authorize target, and the audience the token names), and `OPENBOWER_AUTH_INTERNAL_URL`, the transport this process dials. The data service takes the matching pair.

Run ONE consumer process per lane per deploy (manual, autofill, preview), which is what the compose file ships. The row-claim design is multi-worker safe, but each process enforces a source's declared concurrency ceiling on its own, so a second consumer of the same lane would double the load on that source. Multi-worker scale-out ships as an operated story (docs and compose profiles) when it lands.

