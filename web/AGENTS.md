# The web workspace, conventions for agents

The root AGENTS.md's rules apply throughout; these are the web
module's own, distilled from built code (each rule exists because its
violation shipped once and got caught).

## Contracts and data

- Every wire shape comes from `@bower/schema` (zod generated from the
  Pydantic contract). Enum OPTION LISTS derive from the generated
  schemas too (`AgentConfigSchema.shape.provider.options`), never
  hand-retyped: a value added or removed server-side must reach every
  consumer through the regen.
- Anything read from localStorage is wire data from a PAST app
  version: every field optional on read, enum values validated on
  entry, absence degrading, never crashing. Storage custody (key
  scheme, tolerant read, best-effort writes) lives in one manager
  module per feature, not inline in components.
- Client mirrors of server logic (a key derivation, a token grammar)
  live in ONE module with a comment naming the server home they
  mirror. One fact, one module: every grammar bug so far was two
  inline copies disagreeing.
- Keyset-paged endpoints are WALKED (to a stated bound) by dropdown
  consumers; silently truncating at one page reads as complete when it
  is not.

## Components and state

- A route's feature is a component FAMILY: the orchestrator holds form
  state and composition only; each concern (storage, an async
  lifecycle, a validation model, an actions bar) owns a module in the
  family directory. State lives with the component that owns the
  ritual (a confirm tier's open/closed belongs to the bar that renders
  it).
- Forms whose state initializes from client storage render CLIENT-ONLY
  (`ssr: false` wrapper): a render-time storage read under SSR is a
  hydration mismatch on every visit. Never read browser storage during
  a server-renderable render. Use `next/dynamic` for this, never
  React.lazy + Suspense (which still SSRs the tree); dynamic's
  `loading` slot IS a Suspense fallback, and it renders a SKELETON
  holding the real layout's shape (same grid, same heights, pulse with
  `motion-reduce:animate-none`), never a blank beat.
- Every route group carries `error.tsx` (the house PageState + retry
  idiom, console.error keeping the digest) and `not-found.tsx`; a
  render crash must never reach Next's unstyled default.
- Values that only move together are ONE state object (the model
  address triple): make half-woven states unrepresentable instead of
  coordinating setters.
- Derive, don't duplicate: validity, extracted variables, and other
  computed facts are memos over source state, never parallel state.
- async/await over promise chains (the phase-3 ruling); an effect's
  async work goes in an inner function (the callback's return value is
  the cleanup); `Promise.all` for independent fetches. The one blessed
  `.then` is `dynamic(() => import(...).then(...))`.
- Poll loops carry a generation counter checked after EVERY await
  (including the first) and a hard binary budget when the BROWSER is
  the run's supervisor (the bench); a DURABLE, worker-supervised job
  (a fill) polls open-ended against the worker's heartbeat instead,
  each poll a short bounded request, staleness rendered as a warning
  and never as failure; a superseded loop
  returns silently. Such a loop NEVER stops on transport failures: the
  job runs server-side whatever this page can reach, so repeated
  failures back off toward a ceiling and surface the trouble, and a
  loop that quit while its copy promised updates would be the lie.
- A read whose enum the SERVER owns is tolerant on the client: the
  deployed bundle predates the next contract, and a strict enum turns
  one added member into a whole-page parse failure for every open tab.
  Widen the read, map the unknown member to the value that promises
  LEAST, and pin the mapping.
- Every fetch result goes through `ensureOk` (or an explicit rendered
  failure state); a swallowed non-ok is a silent dead feature.

## Errors, status, and copy

- Only a **400 or a 409** carries user copy and a classifiable code
  through the request funnel. 401 and 403 become `unauthenticated`
  (the login boundary, not a message); every OTHER status renders
  GENERIC_FAILURE with no code, its body being internals. So a server
  refusal that wants its `detail` rendered or its `error` branched on
  must answer 400 or 409: answering 422, or anything else, silently
  loses the copy it wrote and the code the client tests for.
- Every error message lands in one of THREE TIERS, by who can see the
  cause. (1) The server knows: it writes the copy and the client
  renders it VERBATIM (a run's `error`, every 400/409 `detail`
  through the request funnel). (2) The server knows the facts but not
  the surface: it ships STRUCTURED facts on the wire (searches[]
  flags, `truncated`, and `support_followup`, which is a NAMED
  FRAGMENT the client composes into its own sentences, not a finished
  message) and the client phrases them per surface; never whole
  messages on the wire beyond tier 1's own `detail`/`error` (they couple copy to
  backend deploys and forbid per-surface phrasing). (3) Only the
  client can see it (network loss, contract mismatch, poll budget,
  its own validation): the client owns the copy and keeps it GENERAL,
  claiming no knowledge it lacks. A message that hedges about the
  server or its operator is a tier-2/3 message claiming tier-1
  knowledge; compose the server fact (`support_followup`) instead.
- Two severities, from the theme's roles: `warning` (incomplete or
  degraded; nothing is wrong yet) and `danger` (the server refused, or
  this destroys something). NEVER palette classes (`red-600`,
  `amber-500`) at call sites: color utilities go through a role, the
  theme file is the one rebrand surface.
- A blocked action diagnoses per FIELD: the offending sections mark
  themselves (with their one-line cause beside the input), the view
  goes to the first gap, and a readiness checklist sits at the action.
  No compound toasts listing what the UI already knows individually.
  Marks derive live and self-clear; nothing shows before a first
  attempt.
- Copy speaks to the USER's next step, never the operator's: "check
  the server logs" is not an action a hosted user can take. When the
  fix is operational, compose the profile-owned `support_followup`
  fact (and render the clause only when the fact arrived); never
  hedge about an operator the client cannot identify, and never
  reference env files, internal jargon (doors, rosters), or
  code-level concepts in rendered copy. The ONE
  carve-out is a setup walkthrough whose purpose is configuring the
  deployment (the tools card's DataForSEO steps, the
  picker's "Self-hosting?" note): env variable names
  and the .env.example pointer are the actual knobs there, but the
  frame stays "this deployment" and names the hand-off ("if someone
  else runs it, send them this step").
- Copy names causes and next steps in active voice, never moods.
  Labels match the section titles they point at EXACTLY. Empty states
  are invitations with the concrete next step inline (no links to docs
  that do not exist).
- Destructive verbs get one confirm ritual everywhere (swap-in tier,
  focus lands on Cancel, consequence copy), shared, not re-derived per
  surface.

## Accessibility and platform

- The primitives carry the floor: `aria-invalid` rides `invalid`,
  boolean toggles are the Switch primitive (role=switch from Headless
  UI; press-style buttons use `aria-pressed`), navigation landmarks
  are real (`nav` +
  list for breadcrumbs), and focus management uses `autoFocus` on
  fresh mounts; ref-and-querySelector focus is reserved for
  IMPERATIVE gestures outside a mount (the readiness goToSection
  jump, a popover tier swap), never as a render-effect substitute for
  autoFocus. Every pointer
  path has a keyboard path.
- `@bower/ui` stays router-free (no next/link, no navigation APIs;
  the theme provider's next-themes dependency is the one deliberate
  framework tie): navigation-aware components live in the app's
  shared `_components` (in-app links that look like actions go
  through LinkButton; Button's own `href` form is a full-document
  anchor, for boundary crossings only).
- External links: `target="_blank"` pairs with `rel="noreferrer"`. No
  `dangerouslySetInnerHTML`, except the pre-paint head scripts
  `@bower/ui` itself ships (LAYOUT.md requires them; that closed set
  is the whole carve-out). An `href` renders only for values whose
  declared TYPE is url AND whose shape is one.

## Tests

- PURE modules (grammars, derivations, validation models) get node
  tests beside them in app code (`*.test.ts`); packages keep theirs
  in a `tests/` directory (the @bower/api precedent). Both run in the
  `node --test` lane (`make test-web`).
- A leaf's internals graduate to `(app)/_components/` the moment a
  SECOND route composes them, as a family with an index. The agent
  config editor (prompt, model, outputs, tools, and their grammars)
  lived inside the agents builder while the builder was its only
  consumer; the sheet's AI drawer composes the same config, and until
  the move that drawer reached four levels up into a leaf whose index
  exported one component. A leaf's index is what the rest of the app
  may reach for, so a second consumer importing past it is the signal
  to move, not to widen the index.
- A component family whose pure modules outgrow a handful puts them in
  a `lib/` leaf, tests included, leaving the family's own directory to
  its components, its hook, and its index. The split is not about
  tidying tests: a family like the agent builder was nine components
  against fourteen files that render nothing, which is a feature
  directory wearing a component directory's name. `lib/` is what a
  reader can call without a renderer, so it is exactly the half that
  carries tests, and pairing each module with its test stays worth
  reading once the components are not interleaved with them. React
  hooks are NOT lib: they need a renderer, so they sit with the
  components they serve.
- Components are covered by strict tsc + eslint + click-through until a
  component-test lane earns its keep; logic that wants a component test
  should usually be extracted to a pure module instead.
- Never gate a command chain on a piped or echoed exit code: the check
  that is allowed to fail silently will.
