import { AUTH_PROVIDERS, type AuthProvider } from "@bower/api";

// The identity service sends a failed provider sign-in back to the login
// page as ?auth_error=<code>&provider=<name>; the line each code renders
// as the banner at the top of the card. One job per line: what happened,
// and what to do. When the fix lives at the provider, the sentence's own
// verb is the link (`action`), so the banner stays one element in one
// color. A code this web does not know gets the generic line (the
// service may grow codes before the web does).
export type ProviderErrorLine = {
  message: string;
  /** A linked verb inside the sentence: `message` is rendered, then
   * `action.label` as the link, then `action.after`. */
  action?: { href: string; label: string; after: string };
};

// Where each provider lets a person verify an email address.
const EMAIL_SETTINGS: Record<AuthProvider, string> = {
  google: "https://myaccount.google.com/email",
  github: "https://github.com/settings/emails",
};

const PROVIDER_NAMES: Record<AuthProvider, string> = { google: "Google", github: "GitHub" };

function isProvider(name: string | null): name is AuthProvider {
  return name !== null && name in AUTH_PROVIDERS;
}

const FIXED: Record<string, ProviderErrorLine> = {
  account_exists: { message: "An account with this email already exists. Sign in with your password." },
  state_mismatch: { message: "The sign-in attempt was stale or started in a different browser. Try again." },
  provider_unavailable: { message: "That way of signing in isn't available here." },
  provider_cancelled: { message: "Sign-in was cancelled." },
  provider_failed: { message: "The provider couldn't complete the sign-in. Try again, or use your password." },
  rate_limited: { message: "Too many sign-in attempts from your network. Wait a minute, then try again." },
  account_inactive: { message: "This account can't sign in. Contact OpenBower support." },
};

const UNKNOWN: ProviderErrorLine = { message: "Sign-in didn't complete. Try again, or use your password." };

export function providerErrorLine(code: string | null, provider: string | null): ProviderErrorLine | null {
  if (!code) return null;
  if (code === "email_unverified") {
    // Either provider can report an unverified address; the identity
    // service says which one refused.
    if (isProvider(provider)) {
      const name = PROVIDER_NAMES[provider];
      return {
        message: `${name} hasn't verified your primary email. `,
        action: { href: EMAIL_SETTINGS[provider], label: `Verify it with ${name}`, after: ", then try again." },
      };
    }
    return { message: "Your primary email isn't verified with the provider. Verify it there, then try again." };
  }
  return FIXED[code] ?? UNKNOWN;
}
