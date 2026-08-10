import { Suspense } from "react";
import { idpSignup, webRoutes } from "@bower/api";

import { CredentialsForm } from "../_components/credentials-form";

export default function Signup() {
  return (
    <>
      <p className="mb-6 mt-1 text-sm text-ink/60 dark:text-paper/60">Create your account.</p>
      {/* useSearchParams needs a Suspense boundary; nothing here is worth
          blocking pre-render on. */}
      <Suspense>
        <CredentialsForm
          action={idpSignup}
          submitLabel="Create account"
          busyLabel="Creating account…"
          passwordAutoComplete="new-password"
          confirmPassword
          footer={{ prompt: "Already have an account?", label: "Sign in", href: webRoutes.login }}
        />
      </Suspense>
    </>
  );
}
