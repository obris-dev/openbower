import { cookies } from "next/headers";

import { API_URL, API_VERSION } from "./urls";

// The transport base for the calls THIS PROCESS makes, mirroring
// @bower/api's resolveApiInternalBase (this app does not depend on that
// package, so the rule is restated, not imported): truthiness rather
// than ??, because a set-but-empty API_INTERNAL_URL must fall back
// instead of becoming a relative URL, and the trailing slash is trimmed
// so the joined path cannot double up.
const INTERNAL_API_URL = process.env.API_INTERNAL_URL?.replace(/\/$/, "") || API_URL;

/** Server-side session probe: forward the request's cookies to the app
 * backend's me endpoint. Any failure means logged out; the navbar just
 * shows Log in. Requires the session cookie to be visible on this
 * origin (localhost in dev; in production the app backend must scope
 * it to the parent domain). */
export async function isLoggedIn(): Promise<boolean> {
  try {
    const jar = await cookies();
    const cookie = jar.toString();
    if (!cookie.includes("bwr_session")) return false;
    const res = await fetch(`${INTERNAL_API_URL}/${API_VERSION}/auth/me`, { headers: { cookie }, cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
