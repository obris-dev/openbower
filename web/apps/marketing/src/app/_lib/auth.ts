import { cookies } from "next/headers";

import { API_URL } from "./urls";

// This probe runs server-side, where the network path to the backend can
// differ from the browser's (a container reaching a sibling service by its
// compose name). Server-only, so read at runtime, not NEXT_PUBLIC-inlined.
const INTERNAL_API_URL = process.env.API_INTERNAL_URL ?? API_URL;

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
    const res = await fetch(`${INTERNAL_API_URL}/v1/auth/me`, { headers: { cookie }, cache: "no-store" });
    return res.ok;
  } catch {
    return false;
  }
}
