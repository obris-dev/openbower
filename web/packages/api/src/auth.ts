// The auth domain client. Auth rides the HttpOnly session cookie, so every
// app call sends credentials and the browser never touches a token. Login
// is a full-page navigation (the OAuth redirect dance), not an XHR.

import { AuthUserSchema, type AuthUser } from "@bower/schema";

import { apiRoutes, authRoutes, buildApiUrl, buildAuthUrl, resolveAuthBase } from "./routes";

// The identity shape is the generated contract (Pydantic -> JSON Schema ->
// zod), so the web can't drift from what the server serializes.
export type Me = AuthUser;

// Mirror of the backend's SESSION_COOKIE_NAME (auth_client/constants.py):
// the middleware and the server-side guard read the cookie by name.
export const SESSION_COOKIE_NAME = "bwr_session";

// A three-way result so callers can tell "definitely logged out" (the
// server said 401/403) from "we don't know" (network / CORS / 5xx / a body
// that fails schema validation). The hook layer uses this to avoid bouncing
// a user to login over a flaky connection. `fetchMe` is the simple
// Me-or-null wrapper over it.
export type MeResult = { status: "ok"; user: Me } | { status: "unauthenticated" } | { status: "error" };

export async function classifyMe(res: Response): Promise<MeResult> {
  if (res.status === 401 || res.status === 403) return { status: "unauthenticated" };
  if (!res.ok) return { status: "error" };
  try {
    // Validate against the shared schema: a body that doesn't match the
    // contract is an error, not a silently mis-shaped "logged-in" user.
    const parsed = AuthUserSchema.safeParse(await res.json());
    return parsed.success ? { status: "ok", user: parsed.data } : { status: "error" };
  } catch {
    return { status: "error" };
  }
}

export async function fetchMeResult(): Promise<MeResult> {
  let res: Response;
  try {
    res = await fetch(buildApiUrl(apiRoutes.auth.me), { credentials: "include" });
  } catch {
    return { status: "error" };
  }
  return classifyMe(res);
}


/**
 * The signed-in user, or null when there is no live session. Never throws
 * (see fetchMeResult); collapses both "unauthenticated" and "error" to null.
 * Prefer `fetchMeResult` when you must distinguish those.
 */
export async function fetchMe(): Promise<Me | null> {
  const result = await fetchMeResult();
  return result.status === "ok" ? result.user : null;
}

/** Where the Log in button navigates: the backend starts the OAuth flow. */
export function loginUrl(): string {
  return buildApiUrl(apiRoutes.auth.login);
}

// A credential failure, shaped for the error taxonomy: `fields` carries
// per-input messages (tier a, rendered AT the inputs) when the IdP's
// validation response provides them; `message` is the form-level banner
// (tier b) for everything un-attributable (bad credentials, throttled,
// unreachable). Success is null.
export type CredentialFieldErrors = { email?: string[]; password?: string[] };
export type CredentialFailure = { message?: string; fields?: CredentialFieldErrors };

function parseFieldErrors(body: unknown): CredentialFieldErrors | undefined {
  if (typeof body !== "object" || body === null || !("fields" in body)) return undefined;
  const raw = (body as { fields: unknown }).fields;
  if (typeof raw !== "object" || raw === null) return undefined;
  const fields: CredentialFieldErrors = {};
  for (const key of ["email", "password"] as const) {
    const messages = (raw as Record<string, unknown>)[key];
    if (Array.isArray(messages) && messages.every((m) => typeof m === "string") && messages.length > 0) {
      fields[key] = messages;
    }
  }
  return fields.email || fields.password ? fields : undefined;
}

async function postCredentials(
  url: string,
  email: string,
  password: string,
  messages: { unreachable: string; failed: string; on401?: string; on409?: CredentialFailure; badRequest: string },
): Promise<CredentialFailure | null> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    return { message: messages.unreachable };
  }
  if (res.ok) return null;
  if (res.status === 401 && messages.on401) return { message: messages.on401 };
  if (res.status === 409 && messages.on409) return messages.on409;
  if (res.status === 400) {
    const fields = parseFieldErrors(await res.json().catch(() => null));
    // With field association, the inputs speak for themselves (tier a);
    // without it, fall back to the generic banner.
    return fields ? { fields } : { message: messages.badRequest };
  }
  if (res.status === 429) return { message: "Too many attempts. Please wait a moment and try again." };
  return { message: messages.failed };
}

/**
 * Sign in at the identity provider (mints the IdP session cookie). The
 * web tier owns the login SCREEN; the IdP stays the authority this posts
 * to. Returns null on success, or a CredentialFailure to render.
 */
export function idpLogin(email: string, password: string): Promise<CredentialFailure | null> {
  return postCredentials(buildAuthUrl(authRoutes.login), email, password, {
    unreachable: "Sign-in service unreachable. Please try again.",
    failed: "Sign-in failed. Please try again.",
    on401: "Email or password is incorrect.",
    badRequest: "Please enter a valid email and password.",
  });
}

/**
 * Create an account at the identity provider (mints the IdP session on
 * success, like login). Same posture as idpLogin. The duplicate-email
 * 409 is email-attributable, so it renders AT the email field.
 */
export function idpSignup(email: string, password: string): Promise<CredentialFailure | null> {
  return postCredentials(buildAuthUrl(authRoutes.signup), email, password, {
    unreachable: "Sign-up service unreachable. Please try again.",
    failed: "Sign-up failed. Please try again.",
    on409: { fields: { email: ["An account with this email already exists; try signing in."] } },
    badRequest: "Please enter a valid email and a stronger password.",
  });
}

/**
 * Where the browser goes after a successful IdP login: back into the
 * OAuth authorize flow. `next` comes from the IdP's redirect; the server
 * sends it ABSOLUTE (the login page lives on a different host than the
 * IdP), so accept an absolute URL only when its origin IS the IdP, or an
 * IdP-relative path. Anything else is discarded so a crafted link can't
 * bounce a freshly signed-in browser to a foreign host.
 */
export function idpResumeUrl(next: string | null): string {
  const authBase = resolveAuthBase();
  if (next) {
    if (next.startsWith("/") && !next.startsWith("//")) return `${authBase}${next}`;
    try {
      if (new URL(next).origin === new URL(authBase).origin) return next;
    } catch {
      // not a URL; fall through to the safe default
    }
  }
  return `${authBase}/`;
}

/**
 * End the session (server-side revoke + cookie clear). Returns the IdP
 * logout URL the browser must then NAVIGATE to, which ends the identity
 * provider's own session and lands back on the app; skipping that hop
 * leaves the IdP signed in, so the next login skips the password prompt.
 */
export async function logout(): Promise<string | null> {
  try {
    const res = await fetch(buildApiUrl(apiRoutes.auth.logout), {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { idp_logout_url?: string };
    return body.idp_logout_url ?? null;
  } catch {
    // Transport failure: report "no IdP hop" so the caller still clears
    // local UI state instead of hanging on an unhandled rejection.
    return null;
  }
}
