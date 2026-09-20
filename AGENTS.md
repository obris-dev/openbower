# OpenBower, orientation for agents

**What this is.** OpenBower is an open-source, self-hostable prospecting
workspace: a spreadsheet for GTM work where AI research agents fill columns
with evidence-grounded answers, plus an optional hosted company universe
(lookalike discovery) behind an OAuth seam. The open app runs standalone
(CSV import + local models + self-hosted search) at zero cost; the hosted
data service is the paid layer. This repo is the PUBLIC one; the hosted
auth + data services live in a separate private repo.

## Conventions

- uv workspace (Python 3.14, Django, ruff line-length 120, named exceptions
  over blanket catches, enum-as-source-of-truth); pnpm workspace (TypeScript,
  Next.js / React / Tailwind, strict tsc + eslint in CI) for `web/`.
- Settings resolve by environment: `DJANGO_ENV` selects
  `conf/settings/{local,test,cloud}.py`. UNSET REFUSES TO START with a
  one-line error naming the choices: a forgotten variable must never
  silently run permissive settings on a server nor greet a laptop with
  the deployed profile's missing-secret errors. `local` means the repo
  running on your own machine, whatever the packaging (a checkout of
  main or a pinned release), and seeds localhost defaults; `make local`
  and compose set it. `cloud` is the locked-down profile for a deployed
  instance, wherever it's hosted. `test` is what the suite and CI run
  under (no separate ci profile).
- Settings own env access: only `conf/settings/*` reads `os.environ`;
  everything else reads `settings.*`.
- Every wire shape the web consumes crosses through the schema package
  (Pydantic first, zod codegen); the web never hand-types an API response.
- Every list endpoint pages by KEYSET cursor, never LIMIT OFFSET
  (unpaged is allowed only where a HARD CAP bounds the whole
  collection, stated at the cap's constant, as the agents roster's
  MAX_AGENTS does): order
  by `-id` with `?after=<last id>` (ULIDs are time-sortable; helpers in
  the kernel), or by a dense rank where one exists (run results, where
  `rank > :after` also gives cheap random access). `next_cursor` comes
  from the last row of a full page.
- No streaming or server-built file responses. Exports are built
  CLIENT-SIDE from the same paged JSON the views already serve (see the
  sheet CSV export), so the request path stays small, fast,
  bounded JSON. When a file outgrows the browser, the shape is an async
  job writing to a blob store plus a short-lived signed link (the worker
  + poll machinery), never a server-assembled response in between.
- Views stay thin; domain logic lives in services and pure tested modules.
- A function that YIELDS is named `iter_<what>`: the call site has to
  know it is lazy and single-pass, since consuming it twice silently
  yields nothing the second time.
- NO cascades: cross-model refs are id pointers, so the owning service
  deletes its own children in one transaction. A child model added
  without a line in its owner's delete is orphaned forever, invisible
  to every surface that filters by its parent. The one exception is a
  child that settles itself terminally when its parent is gone (an
  autofill run finds its row missing and ends as a ledger row, claimed
  by a picker that never goes through the parent), and an account-level
  singleton with no parent to be orphaned from (the bench node). Both
  are named at the owner's delete, so a reader sees the choice rather
  than a gap.
- An extensible roster is a REGISTRY, never an enum: each member is
  a module that registers itself at its own bottom (register(...)),
  the roster is a directory walk in the app's AppConfig.ready(),
  register() validates all-or-nothing before mutating (collisions
  loud, re-registration idempotent), the wire Literal is held to the
  roster by a boot gate plus a parity pin, and any load-bearing
  ordering is a DECLARED field on the member's spec, never import
  order. Exemplars: agents tools, search providers, inference
  providers, and lists node kinds, where the member IS a typed config
  class carrying its own kind (KIND, DISPLAY, identity()), so a consumer
  constructs an instance and works with it and the kind can never be
  mismatched with its values; a string-keyed lookup exists only for the
  genuinely dynamic read (a stored row whose kind is a column).
- Scheduled work is a crontab line per job (apps/core/crontab, run
  by supercronic in the compose cron service) driving a management
  command over an operation (the reclaim_node_runs command over
  NodeRunFlow.reclaim_stale_processing). Jobs run independently, so every command there must be safe
  to MISS and safe to DOUBLE: a pure age or idempotent judgement,
  never a lock.
- BACKGROUND work a request must not do (a sheet-sized walk, a file
  build) is a Job: a row in the `jobs` app's one table, a kind = a
  typed payload class with a `Progress` cursor and a `run(job,
  progress)` that does ONE bounded slice and hands back where it
  stopped, registered from the owning app's `jobs` package. The
  `jobs` compose service works them within seconds (a loop like the
  provisioners); every transition is a compare-and-set on the job's
  status, `attempts` counts unexpected exits only, and every slice is
  idempotent because a reclaimed job re-walks its last one. A job is
  never a NodeRun (a run is one node applied to one row) and a
  request never walks a sheet: admission decides and queues.
- Which rows a node owes a run to, and how one of its runs EXECUTES,
  is the node kind's PROCESSOR (lists/processors, handed out by
  `processor_for` on the node's kind): `NodeProcessor.enqueue_runs(
  target_list, rows, now)` under a typed `WalkScope` for the walkers,
  which page rows and hand them over knowing no kind and no column;
  `process_run(task, flow)` for a kind whose runs are claimed one at a
  time off the topic, or `process_batch(flow, now)` for a kind that
  claims and settles a node's due runs together, for the dispatchers
  (the node-run consumer, the webhook flush), which claim or iterate
  and hand over knowing no kind. A kind overrides exactly one of the
  two; the other keeps its raising default. The node config classes in
  lists/nodes stay what a node IS at rest.
- An additive NOT NULL column is a STOP-THE-WORLD deploy or a
  three-step (add nullable, deploy the code that writes it, backfill
  then set NOT NULL). Django drops the default after adding the
  column, so any process still running the old code inserts NULL and
  fails until it restarts.
- Unreleased migration chains are COLLAPSED, not accumulated: while
  nothing is released, an app ships one `0001_initial` expressing the end
  state (a chain that records the path, a model created then renamed then
  dropped, is noise to a fresh install). Migration files freeze their
  state values as literals, never app enums, so a rename cannot break a
  fresh migrate. A collapse resets local DBs, so before choosing it,
  confirm the data regenerates: it does today, and it will not always.
- First-pass design rules, learned the hard way:
  - Buy the commodity, own the doctrine: before writing infrastructure,
    ask whether it is product or plumbing an ecosystem already
    maintains (inference runs on pydantic-ai; we own custody, scope,
    grounding, and diagnosis).
  - Use a framework's intended seams. Wrapping objects after
    construction, or state whose writer is not visible in a signature,
    means the idiomatic channel was missed.
  - Typed at construction, one shape end to end: contract models in,
    typed result objects out; no bare dicts or mutable out-params
    across boundaries.
  - No fallbacks on guesses: failure is signal. A blank result with its
    diagnosis attached beats a rescue path that hides what the user
    should see. Delete any branch whose trigger cannot distinguish the
    cases it claims to handle.
  - One path, one guard, one constructor: a second code path needs a
    distinct input, not a distinct fear.
  - Two-tier errors: config errors raise loudly up front (they fail
    every row identically); per-row hazards degrade quietly WITH their
    diagnosis attached.
  - Every empty result carries its why by construction (diagnostics
    ride the result type), and UI copy names causes and next steps.
  - Bounds are named constants, binary for invented numbers, clamping
    (not rejecting) authored input at metered boundaries.
- Web: route groups own their guard and chrome in `layout.tsx`; a
  route's pieces live in its `_components/` leaf, and a component FAMILY
  gets a directory with an index (single files stay flat). Shared hooks
  graduate to a package when a second consumer exists. Route files stay
  thin compositions. UI primitives live in `@bower/ui` (drawers and
  inline editing, no modals).
  Navigation is two-tier: in-app transitions use Link / router.push;
  crossing the auth boundary or any origin (login, logout, the OAuth
  redirect dance) uses window.location, where the full document load is
  required or is itself the point (state reset after the session
  changes).
- House writing rules: no em dashes and no `--` in drafted copy or comments
  (commas or parentheses instead); `|` as the separator in UI copy, never
  middle dots.
- A design doc that doubles as a PR body IS the spec: it changes in
  the same commit as the behavior it describes, and a drift pass
  (phantom fields, superseded defaults, stale numbers, claims with no
  code behind them) is part of done for any change it covers.
- Reversed decisions are marked RULED with what they reversed and
  why, and the superseded passage is REWRITTEN to the new truth,
  never left standing to contradict it; the decision history lives in
  the design doc ONLY (its inline RULED markers). Code comments never carry
  RULED markers, reversal narratives, or proto references: a
  public-repo reader cannot open the design doc, so a shipped comment
  keeps only the constraint half of any ruling.
- Comments state constraints and tradeoffs the code cannot show,
  only: never where a decision came from, what the next line does, or
  why a change was correct.
- No sibling-repo or private-org names in OSS-shipped files (docs,
  comments, code); CLAUDE.md and other non-shipped tooling files are
  exempt.
- Examples are vertical-neutral everywhere (comments, placeholders, docs,
  prompts, test fixtures): `acme.com` / `example.io`, generic industries,
  varied roles. No niche-specific example data in shipped files.
- Branch names `<type>/<kebab-slug>` (`make hooks` installs the check; CI
  enforces).
- A PR delivers end-to-end user-facing value wherever possible: one
  focused value chain per PR, with its tests, docs, and any infra it
  needs riding along, rather than horizontal scaffolding split from the
  feature it serves. Never put assistant session links in commits, PRs,
  or code.
- Commit and push only when asked. Agents prepare changes; the human
  decides when they enter history.
- Users connect their OWN API keys for AI and data providers. Whatever a
  paid provider returns through a user's key is licensed to that user and
  stays in that user's instance: never pool it into a shared or hosted
  store, never expose it to other users, never build a feature that
  depends on passing it along (paid providers' licenses forbid
  redistribution, and this rule is what keeps the product safe to
  open-source). The hosted company universe is a separate thing entirely:
  it is built from data licensed to its operator, never from anything a
  user's key returned.
