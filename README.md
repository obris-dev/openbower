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

A working deploy is the services below plus a one-shot
migrator, and `docker-compose.yml` carries all of them: `make up` on
a fresh clone builds the images, seeds `apps/core/.env` from its
example, and serves. The containers bind-mount the checkout, so edits
hot-reload without a rebuild.

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

`make stop` halts the stack in place and `make up` resumes it;
`make down` removes the containers; the database and the web
dependencies survive in named volumes, while the Python venv is an
anonymous volume the next start re-syncs (`make prune-venvs` clears the
strays). `make reset` removes the named volumes too, which is how you
get a clean database. The stack needs Docker Compose v2.24 or newer. `make logs` tails
everything. SIGTERM or SIGINT lets a consumer's rows in flight finish
before it exits, so restarting one is always safe and can take a while
(a row already talking to a provider is allowed to finish). `make db-up` starts just the database,
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
- Search vendors and wiring live in `config/tools.toml` (`make up`
  seeds it from the template, which lists which vendors serve which
  tools; `manage.py tools` prints the live matrix). Web search runs
  through DuckDuckGo by default (free, keyless). Contact search needs
  a metered vendor's table filled in (Serper, serper.dev); wiring
  `web_search` to it routes web search through it too. On the free door a fill is
  budgeted: one whose search count would exceed the budget is refused
  before it spends, naming the metered next step.

Run ONE consumer process per LANE per deploy (manual, autofill,
preview), which is what the compose file ships. The row-claim design is
multi-worker safe, but each process enforces a source's declared
concurrency ceiling on its own, so a second consumer of the SAME lane
would double the load on that source. A preview run is one row wide,
so the overlap is one request per click in flight; the preview
consumer runs one at a time, so a source declaring a ceiling of N sees
at most one extra row from previews whatever the click rate. Multi-worker scale-out ships as an
operated story (docs and compose profiles) when it lands.
