import { Suspense } from "react";
import { idpLogin, webRoutes } from "@bower/api";

import { CredentialsForm } from "../_components/credentials-form";

export default function Login() {
  return (
    // useSearchParams needs a Suspense boundary; nothing here is worth
    // blocking pre-render on.
    <Suspense>
      <CredentialsForm
        subtitle="Sign in to continue."
        action={idpLogin}
        submitLabel="Sign in"
        busyLabel="Signing in…"
        passwordAutoComplete="current-password"
        footer={{
          prompt: "Don't have an account?",
          label: "Create account",
          href: webRoutes.signup,
        }}
      />
    </Suspense>
  );
}
