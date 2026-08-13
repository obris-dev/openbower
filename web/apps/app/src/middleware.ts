import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE_NAME, loginUrl } from "@bower/api";

/** Cookie-presence gate for the authed surface: a request with no
 * session cookie never renders the app, it goes straight to the OAuth
 * kickoff (no spinner, no flash). Presence only, no validation: a dead
 * cookie still reaches the server-side /me, which owns that verdict. */
export function middleware(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE_NAME)) return NextResponse.next();
  // A failed OAuth callback lands with ?auth_error= and MUST render its
  // terminal state; bouncing it to login would rebuild the redirect
  // loop that page exists to break.
  if (request.nextUrl.searchParams.has("auth_error")) return NextResponse.next();
  return NextResponse.redirect(loginUrl());
}

export const config = {
  // Everything except the auth screens, Next internals, and files.
  matcher: ["/((?!login|signup|_next|.*\\..*).*)"],
};
