import { loginUrl } from "@bower/api";
import { Button } from "@bower/ui";

import type { ModeName } from "./credentials-form/types";

// What a self-hosted install shows instead of a credentials form: its
// own page cannot post credentials to the identity service (cross-site,
// and browsers drop the cookie), so the one button starts the flow that
// signs in at the service's own login page and comes back here.
const COPY: Record<ModeName, { subtitle: string; label: string }> = {
  login: { subtitle: "Sign in with your OpenBower account.", label: "Sign in" },
  signup: { subtitle: "Sign in, or create your OpenBower account.", label: "Sign in or create an account" },
};

export function HubSignIn({ mode }: { mode: ModeName }) {
  const { subtitle, label } = COPY[mode];
  return (
    <>
      <p className="mb-6 mt-1 text-sm text-muted">{subtitle}</p>
      {/* A full document load on purpose: the flow crosses the auth
          boundary (see the navigation rule). */}
      <Button href={loginUrl()} fullWidth>
        {label}
      </Button>
      <p className="mt-6 text-center text-xs text-faint">
        You sign in on your OpenBower account page and come back to this install.
      </p>
    </>
  );
}
