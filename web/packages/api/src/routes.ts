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
// one-line edit here rather than a string hunt across components. Routes
// for a domain land with the phase that serves it.

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
} as const;

// IdP endpoints (relative to NEXT_PUBLIC_AUTH_URL; see resolveAuthBase).
export const authRoutes = {
  // POST: mint the IdP session (the web login form posts credentials here).
  login: `/${AUTH_API_VERSION}/auth/login`,
  // POST: create the account + mint the session (the signup form posts here).
  signup: `/${AUTH_API_VERSION}/auth/signup`,
} as const;

// Next.js frontend paths the browser navigates to programmatically.
export const webRoutes = {
  home: "/",
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

/** App backend origin (no trailing slash). Throws in prod if unset. */
export function resolveApiBase(): string {
  return resolveOrigin(process.env.NEXT_PUBLIC_API_URL, "http://localhost:8002", "NEXT_PUBLIC_API_URL");
}

/** IdP origin (no trailing slash). Throws in prod if unset. */
export function resolveAuthBase(): string {
  return resolveOrigin(process.env.NEXT_PUBLIC_AUTH_URL, "http://localhost:8001", "NEXT_PUBLIC_AUTH_URL");
}

/** Join the app base with an `apiRoutes` path (absolute URLs pass through). */
export function buildApiUrl(path: string): string {
  return path.startsWith("http") ? path : `${resolveApiBase()}${path}`;
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
