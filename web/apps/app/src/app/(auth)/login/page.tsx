import { Suspense } from "react";
import { idpLogin, webRoutes } from "@bower/api";

import { CredentialsForm } from "../_components/credentials-form";

export default function Login() {
  return (
    <>
      <p className="mb-6 mt-1 text-sm text-ink/60 dark:text-paper/60">Sign in to continue.</p>
      {/* useSearchParams needs a Suspense boundary; nothing here is worth
          blocking pre-render on. */}
      <Suspense>
        <CredentialsForm
          action={idpLogin}
          submitLabel="Sign in"
          busyLabel="Signing in…"
          passwordAutoComplete="current-password"
          footer={{ prompt: "Don't have an account?", label: "Create account", href: webRoutes.signup }}
        />
      </Suspense>
    </>
  );
}
