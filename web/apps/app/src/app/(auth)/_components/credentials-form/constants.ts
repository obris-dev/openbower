// A cookie, NOT localStorage, so the SERVER login page can render the
// box filled and checked on first paint: localStorage is invisible to
// SSR and forces a post-hydration flip (a visible flash). A UX
// preference, not a credential, hence readable by script.
export const REMEMBER_EMAIL_COOKIE = "bwr_remember_email";
export const REMEMBER_MAX_AGE_SECONDS = 180 * 24 * 3600;

/** Decode a cookie value that any script could have written: a stray %
 * makes decodeURIComponent THROW, and a URIError from a malformed
 * preference cookie must never take down the login page. */
export function safeDecode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}
