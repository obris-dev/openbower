"use client";

import { ShieldAlert } from "lucide-react";
import { Button, Card, CopyButton } from "@bower/ui";
import { loginUrl } from "@bower/api";

/** Terminal full-page state for a failed OAuth callback (?auth_error=).
 * Deliberately NOT an auto-redirect: the IdP session usually survives
 * the failure, so bouncing back to login auto-approves and reproduces
 * the same failure in a loop. The user restarts the flow explicitly. */

// Codes come from the API's AuthErrorCode enum; anything unrecognized
// gets the generic line (the server may grow codes before the web does).
const DETAILS: Record<string, string> = {
  state_mismatch: "The attempt was stale or started in a different browser.",
  missing_params: "The sign-in response was incomplete.",
  login_failed: "The identity service couldn't complete the sign-in.",
};

// The hosted deployment opens by invitation. This landing is not a
// failure to retry (the identity session is fine; a retry refuses the
// same way), so it names the way to run OpenBower now.
const HOSTED_ACCESS_REQUIRED = "hosted_access_required";
const INSTALL_LINE = "curl -fsSL https://openbower.ai | sh";

export function SignInFailed({ code }: { code: string }) {
  if (code === HOSTED_ACCESS_REQUIRED) {
    return (
      <main className="grid min-h-dvh place-items-center bg-canvas p-6">
        <Card className="w-full max-w-sm p-8 text-center" role="status">
          <ShieldAlert aria-hidden className="mx-auto h-10 w-10 text-faint" />
          <h1 className="mt-4 text-lg font-semibold text-foreground">You&apos;re on the list</h1>
          <p className="mt-2 text-sm text-muted">
            Your account is on the list for the hosted app. Until your invite, OpenBower runs on your own machine:
          </p>
          <div className="mt-4 flex items-center gap-2 rounded-md bg-surface py-2 pl-3 pr-2">
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-left text-xs text-foreground">
              {INSTALL_LINE}
            </code>
            <CopyButton value={INSTALL_LINE} iconOnly />
          </div>
        </Card>
      </main>
    );
  }
  return (
    <main className="grid min-h-dvh place-items-center bg-canvas p-6">
      <Card className="w-full max-w-sm p-8 text-center" role="alert">
        <ShieldAlert aria-hidden className="mx-auto h-10 w-10 text-faint" />
        <h1 className="mt-4 text-lg font-semibold text-foreground">Sign-in didn&apos;t complete</h1>
        <p className="mt-2 text-sm text-muted">{DETAILS[code] ?? "Something went wrong finishing sign-in."}</p>
        <Button
          fullWidth
          className="mt-5"
          onClick={() => {
            // Full document load on purpose: restarting the OAuth flow
            // crosses the auth boundary (see the navigation rule). No
            // return path: this page's own URL carries the error.
            window.location.href = loginUrl();
          }}
        >
          Try again
        </Button>
      </Card>
    </main>
  );
}
