// Centralized route registry for the OpenBower web app.
//
// Three namespaces, because this app is an OAuth client of a SEPARATE
// identity provider:
//   - `apiRoutes`  : the app backend's HTTP endpoints the browser calls.
//   - `authRoutes` : the IdP endpoints the browser calls directly (the
//                    login form posts here; the IdP serves no UI of its own).
//   - `webRoutes`  : Next.js frontend paths the browser navigates to.
//
// Two origins (the app and the IdP) and two version prefixes, so the two
// services can bump their API versions independently. A path change is a
// one-line edit here rather than a string hunt across components.

const API_VERSION = process.env.NEXT_PUBLIC_API_VERSION ?? "v1";
// The IdP's own version prefix, mirrored separately so the app and IdP
// prefixes are never assumed identical.
const AUTH_API_VERSION = process.env.NEXT_PUBLIC_AUTH_API_VERSION ?? "v1";

// App backend (relative to NEXT_PUBLIC_API_URL; see resolveApiBase).
export const apiRoutes = {
  auth: {
    // GET: starts the OAuth flow (a redirect to the IdP authorize URL).
    login: `/${API_VERSION}/auth/login`,
    // GET: the IdP redirect target; exchanges the code, sets the cookie.
    callback: `/${API_VERSION}/auth/callback`,
    // POST: revoke the app session; returns the IdP logout URL.
    logout: `/${API_VERSION}/auth/logout`,
    // GET: the signed-in user projection.
    me: `/${API_VERSION}/auth/me`,
  },
  discover: {
    // POST: the look-alike query (202 + run for cold cohorts, 200 cached).
    lookalikes: `/${API_VERSION}/discover/lookalikes`,
    // GET: poll the run to a terminal status.
    lookalikeRun: (id: string) => `/${API_VERSION}/discover/lookalikes/runs/${id}`,
    // POST: stop a pending/running run (terminal runs no-op).
    lookalikeRunCancel: (id: string) => `/${API_VERSION}/discover/lookalikes/runs/${id}/cancel`,
    // POST: snapshot a COMPLETE run into a local list.
    lookalikeRunSaveList: (id: string) => `/${API_VERSION}/discover/lookalikes/runs/${id}/save-list`,
  },
  agents: {
    // GET the roster / POST create.
    index: `/${API_VERSION}/agents`,
    // What THIS deploy can run (models + search availability).
    catalog: `/${API_VERSION}/agents/catalog`,
    // GET / PATCH / DELETE one agent.
    detail: (id: string) => `/${API_VERSION}/agents/${id}`,
  },
  webhooks: {
    // GET the roster / POST create (the create response carries the
    // signing secret once).
    index: `/${API_VERSION}/webhooks`,
    // GET / PATCH / DELETE one destination.
    detail: (id: string) => `/${API_VERSION}/webhooks/${id}`,
    // POST: one signed test delivery, sent now; 200 with the delivery.
    test: (id: string) => `/${API_VERSION}/webhooks/${id}/test`,
    // POST: a new signing secret, shown once (the create response's shape).
    rotate: (id: string) => `/${API_VERSION}/webhooks/${id}/rotate`,
    // GET: keyset deliveries by -id (?after=&limit=).
    deliveries: (id: string) => `/${API_VERSION}/webhooks/${id}/deliveries`,
  },
  runs: {
    // POST a drafted config + one hand-fed row; 202 + a bench run to poll.
    bench: `/${API_VERSION}/runs/bench`,
    // GET: one bench run by id (its result rides it once it finished).
    detail: (id: string) => `/${API_VERSION}/runs/${id}`,
    // POST: abandon an unclaimed bench run; a running or terminal run no-ops.
    cancel: (id: string) => `/${API_VERSION}/runs/${id}/cancel`,
  },
  lists: {
    // GET: keyset index (?after=). POST: create.
    index: `/${API_VERSION}/lists`,
    // GET: the whole folder set. POST: create.
    folders: `/${API_VERSION}/lists/folders`,
    // PATCH {label} / DELETE (lists inside go loose).
    folder: (id: string) => `/${API_VERSION}/lists/folders/${id}`,
    // POST multipart {file, label?}: a CSV becomes a sheet.
    import: `/${API_VERSION}/lists/import`,
    // GET / PATCH {label} / DELETE.
    detail: (id: string) => `/${API_VERSION}/lists/${id}`,
    // GET: keyset rows by position (?after=&limit=). POST: append rows.
    rows: (id: string) => `/${API_VERSION}/lists/${id}/rows`,
    // POST: append one blank column (no fill attached).
    columns: (id: string) => `/${API_VERSION}/lists/${id}/columns`,
    // PATCH {keys}: reorder the sheet's columns (the whole order; the
    // server refuses anything that is not a permutation of what it
    // holds).
    columnOrder: (id: string) => `/${API_VERSION}/lists/${id}/column-order`,
    column: (id: string, key: string) => `/${API_VERSION}/lists/${id}/columns/${encodeURIComponent(key)}`,
    // POST: add an AI column and admit its fill in one transaction.
    aiColumn: (id: string) => `/${API_VERSION}/lists/${id}/columns/ai`,
    // POST: one sample digest to a destination, sent now; 200 with the delivery.
    columnWebhookTest: (id: string) => `/${API_VERSION}/lists/${id}/columns/webhook/test`,
    // POST {label, destination_id, wait_keys, payload_keys, interval_seconds}: add a webhook column; 201 with the summary.
    columnWebhook: (id: string) => `/${API_VERSION}/lists/${id}/columns/webhook`,
    // POST: the envelope a test of this body would carry, rendered server-side, sent nowhere.
    columnWebhookPreview: (id: string) => `/${API_VERSION}/lists/${id}/columns/webhook/preview`,
    // GET / PATCH one webhook column's config.
    columnWebhookConfig: (id: string, key: string) => `/${API_VERSION}/lists/${id}/columns/${key}/webhook`,
    // POST: refill a column's unanswered rows (a NEW run, fresh snapshot).
    columnRefill: (id: string, key: string) => `/${API_VERSION}/lists/${id}/columns/${key}/refill`,
    // PATCH {prompt}: edit the column's fill prompt (reaches the NEXT fill).
    columnPrompt: (id: string, key: string) => `/${API_VERSION}/lists/${id}/columns/${key}/prompt`,
    // GET: keyset fill runs by -id (?after=), ALL states first-class.
    fills: (id: string) => `/${API_VERSION}/lists/${id}/fills`,
    // POST: stop a live fill (an already-terminal run no-ops).
    fillCancel: (id: string, runId: string) => `/${API_VERSION}/lists/${id}/fills/${runId}/cancel`,
  },
} as const;

// IdP endpoints (relative to NEXT_PUBLIC_AUTH_URL; see resolveAuthBase).
export const authRoutes = {
  // POST: mint the IdP session (the web login form posts credentials here).
  login: `/${AUTH_API_VERSION}/auth/login`,
  // POST: create the account + mint the session (the signup form posts here).
  signup: `/${AUTH_API_VERSION}/auth/signup`,
} as const;

const LIST_PREFIX = "/lists";
const SETTINGS_PREFIX = "/settings";

// Next.js frontend paths the browser navigates to programmatically.
export const webRoutes = {
  // Home IS the lists index (sheets are the unit of work).
  home: "/",
  lists: "/",
  // The detail prefix: nav highlighting matches on it, list() builds on it.
  listPrefix: LIST_PREFIX,
  list: (id: string) => `${LIST_PREFIX}/${id}`,
  discover: "/discover",
  agents: "/agents",
  agentNew: "/agents/new",
  agent: (id: string) => `/agents/${id}`,
  // The account area: a hub of sections, reached from the user menu.
  settingsPrefix: SETTINGS_PREFIX,
  settings: SETTINGS_PREFIX,
  settingsAccount: `${SETTINGS_PREFIX}/account`,
  settingsWebhooks: `${SETTINGS_PREFIX}/webhooks`,
  settingsWebhook: (id: string) => `${SETTINGS_PREFIX}/webhooks/${id}`,
  login: "/login",
  signup: "/signup",
} as const;

function resolveOrigin(fromEnv: string | undefined, devFallback: string, varName: string): string {
  const trimmed = fromEnv?.replace(/\/$/, "");
  if (trimmed) return trimmed;
  if (process.env.NODE_ENV === "production") {
    throw new Error(`${varName} is required in production. Set it at build time to the service origin.`);
  }
  return devFallback;
}

/** The app backend's IDENTITY: the origin the BROWSER dials. Throws in
 * prod if unset. Every value handed to a browser (a redirect Location,
 * an href, a serialized prop) comes from here. */
export function resolveApiBase(): string {
  return resolveOrigin(process.env.NEXT_PUBLIC_API_URL, "http://localhost:8002", "NEXT_PUBLIC_API_URL");
}

/** TRANSPORT for fetches this PROCESS makes, which is a different fact
 * from the identity above: a container's localhost is itself, so compose
 * supplies API_INTERNAL_URL as the sibling-service route. Server-only and
 * runtime-read; absent, it collapses to the identity, so host runs and
 * hosted deploys configure nothing.
 *
 * Never hand this to a browser: `core:8002` is a compose service name
 * that resolves nowhere outside the network. That is why the two bases
 * are separate functions and the fetch funnel is the only caller (the
 * same split auth_client/idp_urls.py draws server-side). */
function resolveApiInternalBase(): string {
  if (typeof window === "undefined") {
    const internal = process.env.API_INTERNAL_URL?.replace(/\/$/, "");
    if (internal) return internal;
  }
  return resolveApiBase();
}

/** IdP origin (no trailing slash). Throws in prod if unset. No internal
 * twin on purpose: nothing server-side here calls the IdP (the browser
 * navigates to it, and Django owns the server-to-server calls through
 * its own OPENBOWER_AUTH_INTERNAL_URL). */
export function resolveAuthBase(): string {
  return resolveOrigin(process.env.NEXT_PUBLIC_AUTH_URL, "http://localhost:8001", "NEXT_PUBLIC_AUTH_URL");
}

/** Join the app's IDENTITY base with an `apiRoutes` path (absolute URLs
 * pass through). For URLs the browser will follow. */
export function buildApiUrl(path: string): string {
  return path.startsWith("http") ? path : `${resolveApiBase()}${path}`;
}

/** Join the TRANSPORT base with an `apiRoutes` path. For the fetch
 * funnel only (see resolveApiInternalBase). */
export function buildApiFetchUrl(path: string): string {
  return path.startsWith("http") ? path : `${resolveApiInternalBase()}${path}`;
}

/** Join the IdP base with an `authRoutes` path (absolute URLs pass through). */
export function buildAuthUrl(path: string): string {
  return path.startsWith("http") ? path : `${resolveAuthBase()}${path}`;
}

/**
 * Append `?next=<path>` to a web route for carrying the IdP authorize `next`
 * when cross-linking login <-> signup. Returns the path unchanged if `next`
 * is empty.
 */
export function withNext(path: string, next: string | null | undefined): string {
  if (!next) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}next=${encodeURIComponent(next)}`;
}
