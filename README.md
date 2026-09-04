<p align="center">OpenBower</p>

<p align="center">
  Open-source prospecting: the GTM workspace that's yours.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0%20with%20terms-blue.svg" alt="License: Apache 2.0 with additional terms"></a>
</p>

---

OpenBower is a spreadsheet for go-to-market work: import any CSV of
companies or leads, add AI research columns that gather evidence from the
web and fill cells with grounded answers, and (optionally) discover
similar companies from a hosted universe. Your workflows, your data, your
API keys.

**Status: under construction.** The product is being built in public,
phase by phase; each merged phase leaves working end-to-end functionality.
Run requirements and setup docs land with the phases that need them.

## Running it (the self-host footprint)

A working deploy is six long-running services plus a one-shot
migrator, and `docker-compose.yml` carries all of them: `make up` on
a fresh clone builds the images, seeds `apps/core/.env` from its
example, and serves. The containers bind-mount the checkout, so edits
hot-reload without a rebuild.

| Service | What it runs | Where to look |
|---------|--------------|---------------|
| db      | Postgres 16 (host port 5433) | `make local-dbshell` for psql |
| core-setup | migrations and the cache table, once per start; core, the workers, and the cron wait for it to finish | `make logs` |
| core    | the Django api on :8002 | `make logs-core` |
| worker  | `manage.py fill_worker --kinds normal`, the background process that claims fill rows in batches, runs the research agents, and writes cells and outcomes | `make logs-worker` |
| worker-test | the same binary serving only test-kind fills (the bench's one-row diagnostics), so bench latency never queues behind a wide fill | `make logs-worker` |
| cron    | supercronic over `apps/core/crontab`: scheduled maintenance, today the hourly purge of test fills older than a day | `make logs-cron` |
| web     | the Next.js apps: the product app on :3003, the marketing site on :3005 | `make logs-web` |

`make stop` halts the stack in place and `make up` resumes it;
`make down` removes the containers; the database and the web
dependencies survive in named volumes, while the Python venv is an
anonymous volume the next start re-syncs (`make prune-venvs` clears the
strays). `make reset` removes the named volumes too, which is how you
get a clean database. The stack needs Docker Compose v2.24 or newer. `make logs` tails
everything. Compose runs exactly ONE worker PER FILL KIND (a normal
one and a test one). The worker takes `--once`
(exit when no fill has claimable work, the suite's smoke). SIGTERM
or SIGINT lets the NORMAL worker's rows in flight finish before it
exits, so restarting it is always safe and can take a while (a row
already talking to a provider is allowed to finish); the test
worker's short grace kills its bench row instead, costing one metered
call and one counted attempt on a throwaway diagnostic. `make db-up` starts just the database,
which is what the host-run test suite needs.

Signing in needs an identity provider, which is a SEPARATE service (the
hosted one, or a local stack in development) along with the data service
behind look-alike discovery. The containers reach both by name over a
docker network called `openbower-suite`, which `make up` creates; a
bare `docker compose up` will refuse until that network exists. Repointing the app at a different identity provider means setting both
halves: `OPENBOWER_AUTH_URL`, the IDENTITY (the browser's authorize
target, and the audience the token names), and
`OPENBOWER_AUTH_INTERNAL_URL`, the transport this process dials. The
data service takes the matching pair. The two halves take DIFFERENT
channels under compose: the identity pair is deliberately absent from
the `environment:` block, so it comes from `apps/core/.env`, while the
transport pair is interpolated there and so comes from your shell or a
project-root `.env`.

Environment facts the footprint needs (`apps/core/.env.example`
carries the full annotated list):

- `DJANGO_ENV` is always explicit (`local` for a machine you own,
  `cloud` for a deployed instance; unset refuses to start). The api
  and the worker read the same settings profile, so they see the same
  providers and search configuration.
- Inference sources (structure AND keys) live in one file,
  `config/providers.toml` (gitignored; copy
  `config/templates/providers.example.toml` and edit, or point
  `PROVIDERS_CONFIG` at another path). Section names are the
  registered provider specs, validated at boot (a typo'd section
  refuses startup with the section named). `make up` seeds the file
  from the template when it is absent, which starts you with a local
  Ollama entry; edit the file for real sources and keys.
- Search runs through DuckDuckGo by default (free, keyless). Set
  `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` for metered search:
  contact search requires it, `SEARCH_PROVIDER=dataforseo` routes web
  search through it too. Without those credentials a fill still runs
  on the free door, but it is budgeted: a fill whose search count
  would exceed that budget is refused before it spends, naming the
  paid provider.

Run ONE worker process per FILL KIND per deploy, which is what the
compose file ships. The row-claim design is multi-worker safe, but
each process enforces a source's declared concurrency ceiling on its
own, so a second worker of the SAME kind would double the load on
that source. Each test FILL is one row wide, so in the common case
(one bench click at a time) the overlap is a single request; but the
one-live-test rule is per account and advisory, so a source declaring
a ceiling of N can see up to N extra rows while several accounts
bench at once;
multi-worker scale-out ships as an operated story (docs and compose
profiles) when it lands.
