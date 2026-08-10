import { Suspense } from "react";
import { idpSignup, webRoutes } from "@bower/api";

import { CredentialsForm } from "../_components/credentials-form";

export default function Signup() {
  return (
    // useSearchParams needs a Suspense boundary; nothing here is worth
    // blocking pre-render on.
    <Suspense>
      <CredentialsForm
        subtitle="Create your account."
        action={idpSignup}
        submitLabel="Create account"
        busyLabel="Creating account…"
        passwordAutoComplete="new-password"
        confirmPassword
        footer={{
          prompt: "Already have an account?",
          label: "Sign in",
          href: webRoutes.login,
        }}
      />
    </Suspense>
  );
}
