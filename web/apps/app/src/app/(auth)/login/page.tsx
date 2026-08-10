import { Suspense } from "react";

import { CredentialsForm, CredentialsFormFallback } from "../_components/credentials-form";

export default function Login() {
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="login" />}>
      <CredentialsForm mode="login" />
    </Suspense>
  );
}
