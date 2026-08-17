// Directive-free on purpose: the server page reads this cookie name, and
// a constant exported from a "use client" module reaches server code as
// a client REFERENCE, not a string (silent lookup failure).

// A COOKIE, not localStorage: collapse shapes the server-rendered rows,
// and only a cookie is visible to that render, so the first paint is
// already right. A UX preference, not a credential, hence script-readable.
export const COLLAPSED_COOKIE = "bwr_lists_collapsed";
