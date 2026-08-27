// The other origins this site links out to. NEXT_PUBLIC_* is inlined at
// build; localhost fallbacks keep dev working with zero config.
export const APP_URL = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3003";
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";

export const GITHUB_URL = "https://github.com/obris-dev/openbower";

// The repo README is the documentation until a docs site exists; every
// docs link routes through this one constant so the swap is one line.
export const DOCS_URL = `${GITHUB_URL}#readme`;
