"use client";

import { ShieldAlert } from "lucide-react";
import { Button, Card } from "@bower/ui";
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

export function SignInFailed({ code }: { code: string }) {
  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <Card className="w-full max-w-sm p-8 text-center" role="alert">
        <ShieldAlert aria-hidden className="mx-auto h-10 w-10 text-ink/30 dark:text-paper/30" />
        <h1 className="mt-4 text-lg font-semibold text-ink dark:text-paper">Sign-in didn&apos;t complete</h1>
        <p className="mt-2 text-sm text-ink/60 dark:text-paper/60">
          {DETAILS[code] ?? "Something went wrong finishing sign-in."} Nothing was changed on your account.
        </p>
        <Button
          fullWidth
          className="mt-5"
          onClick={() => {
            // Full document load on purpose: restarting the OAuth flow
            // crosses the auth boundary (see the navigation rule).
            window.location.href = loginUrl();
          }}
        >
          Try again
        </Button>
      </Card>
    </main>
  );
}
