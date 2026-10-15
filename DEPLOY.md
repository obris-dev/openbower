# Deploying OpenBower

The self-host footprint: what `docker-compose.yml` runs, how to operate it, where to look when something is off, and the environment facts a deploy needs. The [README](README.md) covers the quickstart and the sign-in prerequisite.

## Operating an install

The install puts one command on your PATH, `bower-admin`: a shim in `~/.local/bin` that runs `scripts/bower-admin.sh` inside the checkout, plus one guarded `export PATH` line appended to your shell's rc files when that directory is not on your PATH yet (zsh, bash, and `.profile`; `OPENBOWER_NO_MODIFY_PATH=1` skips it and prints the line instead). From a plain clone, `sh scripts/bower-admin.sh <command>` is the same thing. The installer the one-liner runs is `scripts/install.sh`, also served as a plain file at https://openbower.ai/install.sh for reading first.

| Command | Does |
|---------|------|
| `install` | first-time setup: the prerequisites checklist, build and start, the model setup, the shim on your PATH |
| `up [service...]` | start the stack; seeds the config files when absent and runs `setup` until `config/providers.toml` exists |
| `build` | rebuild the images and start (after a dependency change) |
| `stop` / `down` / `restart [service...]` | stop in place (a consumer finishes its row first; `up` resumes), stop and remove the containers (the database volume survives), restart in place |
| `upgrade [tag\|branch]` | bare: move forward to the newest release tag and rebuild; named: move to that tag or branch in either direction, with a one-line warning when the target lacks this checkout's commit |
| `status` / `logs [service...]` / `version` | what is running, the logs (all or one service), the installed and newest versions |
| `tools` / `dbshell` | the tool matrix (which vendor serves each tool, and its readiness); a psql shell on the database |
| `doctor` | the prerequisites checklist on its own |
| `reload` | apply a hand edit to `config/providers.toml` or `config/tools.toml`: restarts the services that read them |
| `setup` / `providers` | configure where models run (`config/providers.toml`) and the search vendor (`config/tools.toml`). `providers` prints what each source serves, or why it cannot |

`setup` writes keys inline into `config/providers.toml` and `config/tools.toml`.

## The services

A working deploy is the services below plus a one-shot migrator; `docker-compose.yml` carries all of them.

| Service | What it runs | Where to look |
|---------|--------------|---------------|
| db      | Postgres 16 (host port 5433) | `bower-admin dbshell` for psql |
| core-setup | migrations and the cache table, once per start; core, the workers, and the cron wait for it to finish | `bower-admin logs core-setup` |
| core    | the Django api on :8002 | `bower-admin logs core` |
| jobs    | the background jobs loop (`manage.py run_jobs`): a fill's walk over its sheet, the webhook backfill, the rank re-space | `bower-admin logs jobs` |
| fill-provisioner, fill-consumer | the manual fill lane: the provisioner publishes an open fill's queued runs to the bus, the consumer claims each, runs the research agent, and lands the cell | `bower-admin logs fill-provisioner fill-consumer` |
| autofill-provisioner, autofill-consumer | the same two roles for automatic runs (a pushed row's AI columns) | `bower-admin logs autofill-provisioner autofill-consumer` |
| preview-consumer | the builder's Test on its own lane: the autofill provisioner routes a preview run here, so a watched one-row diagnostic never waits behind a fill or a pushed-row burst | `bower-admin logs preview-consumer` |
| ingest-worker | the webhook ingest consumer | `bower-admin logs ingest-worker` |
| cron    | supercronic over `apps/core/crontab`: scheduled maintenance (the hourly prune of preview runs older than a day, the stale-run reclaim, the deferred flush and delivery prune) | `bower-admin logs cron` |
| web     | the Next.js apps: the product app on :3003, the marketing site on :3004 | `bower-admin logs web` |

The database and the web dependencies survive in named volumes; the Python venv is an anonymous volume the next start re-syncs; a stray one per upgrade is the only accumulation, and Docker's own `docker volume prune` reclaims them. SIGTERM or SIGINT lets a consumer's rows in flight finish before it exits, so stopping or restarting one is always safe and can take a while. `bower-admin up db` starts just the database, which is what the host-run test suite needs.

Environment facts (`apps/core/.env.example` carries the full annotated list):

- `DJANGO_ENV` is always explicit (`local` for a machine you own, `cloud` for a deployed instance; unset refuses to start). The api and the workers read the same settings profile, so they see the same providers and search configuration.
- Repointing the app at a different identity provider means setting both halves: `OPENBOWER_AUTH_URL`, the identity (the browser's authorize target, and the audience the token names), and `OPENBOWER_AUTH_INTERNAL_URL`, the transport this process dials. The data service takes the matching pair.

Run ONE consumer process per lane per deploy (manual, autofill, preview), which is what the compose file ships. The row-claim design is multi-worker safe, but each process enforces a source's declared concurrency ceiling on its own, so a second consumer of the same lane would double the load on that source. Multi-worker scale-out ships as an operated story (docs and compose profiles) when it lands.

