// @bower/api/server: requests Next's SERVER makes on the user's behalf
// (forwarded session cookie, never credentials: "include"). The
// server-only guard turns any client-side import into a build error, so
// cookie-forwarding code cannot leak into the bundle.
import "server-only";

import { classifyMe, type MeResult } from "./auth";
import { apiRoutes, buildApiUrl } from "./routes";

/** The server-side /me: the layout forwards the session cookie it
 * received, so the authed shell renders with the user already resolved
 * (no client spinner, no refetch). Never cached. */
export async function fetchMeResultWithCookie(cookieHeader: string): Promise<MeResult> {
  let res: Response;
  try {
    res = await fetch(buildApiUrl(apiRoutes.auth.me), { headers: { cookie: cookieHeader }, cache: "no-store" });
  } catch {
    return { status: "error" };
  }
  return classifyMe(res);
}
