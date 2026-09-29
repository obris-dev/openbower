// The other origins this site links out to. NEXT_PUBLIC_* is inlined at
// build; localhost fallbacks keep dev working with zero config.
// Both trimmed and truthiness-checked, matching @bower/api's
// resolveOrigin: a set-but-empty var must fall back rather than become a
// relative URL, and a trailing slash would otherwise produce `//login`,
// which the app's middleware matcher does not treat as the login path.
export const APP_URL = (process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3003").replace(/\/$/, "");
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002").replace(/\/$/, "");
// The identity provider, which also takes the public waitlist post: a
// marketing launch needs it and this site, and no app backend.
export const AUTH_URL = (process.env.NEXT_PUBLIC_AUTH_URL || "http://localhost:8001").replace(/\/$/, "");
// The app backend's version prefix. Same variable NAME @bower/api reads;
// the default is a second copy, so a version bump has to move both.
export const API_VERSION = process.env.NEXT_PUBLIC_API_VERSION ?? "v1";

export const GITHUB_URL = "https://github.com/obris-dev/openbower";

// The repo README is the documentation until a docs site exists; every
// docs link routes through this one constant so the swap is one line.
export const DOCS_URL = `${GITHUB_URL}#readme`;

