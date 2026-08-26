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

A working deploy is four services. `docker-compose.yml` carries the
database; the other three run as processes, and a containerized deploy
runs the same commands as services next to the compose `db`:

| Service | What it runs | Local target |
|---------|--------------|--------------|
| db      | Postgres 16 (the compose service; host port 5433 locally) | `make db-up` |
| api     | the Django backend on :8002 | `make api-local` |
| worker  | `manage.py fill_worker`, the background process that claims fill rows in batches, runs the research agents, and writes cells and outcomes | `make worker-local` |
| web     | the Next.js app on :3003 | `make web-local` |

`make local` starts api, worker, and web detached with the db up;
`make stop` stops them, and `make logs` tails
`/tmp/openbower/api.log`, `/tmp/openbower/worker.log`, and
`/tmp/openbower/web.log`. The worker takes `--once` (exit when no job
has claimable work, the CI smoke), and SIGTERM or SIGINT lets rows in
flight finish before it exits, so a supervisor can restart it safely.

Environment facts the footprint needs (`apps/core/.env.example`
carries the full annotated list):

- `DJANGO_ENV` is always explicit (`local` for a machine you own,
  `cloud` for a deployed instance; unset refuses to start). The api
  and the worker read the same settings profile, so they see the same
  providers and search configuration.
- Inference sources (structure AND keys) live in one file,
  `config/providers.toml` (gitignored; copy
  `config/templates/providers.example.toml` and edit, or point
  `PROVIDERS_CONFIG` at another path). Local runs add a keyless
  `ollama` source automatically.
- Search runs through DuckDuckGo by default (free, keyless). Set
  `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD` for metered search:
  contact search requires it, `SEARCH_PROVIDER=dataforseo` routes web
  search through it too. Without those credentials a fill still runs
  on the free door, but it is budgeted: a fill whose search count
  would exceed that budget is refused before it spends, naming the
  paid provider.

Run ONE worker process per deploy. The row-claim design is
multi-worker safe, but each worker enforces a source's declared
concurrency ceiling on its own, so N workers would put N times the
declared load on that source; multi-worker scale-out ships as an
operated story (docs and compose profiles) when it lands.
