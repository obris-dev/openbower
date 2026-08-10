import { Suspense } from "react";

import { CredentialsForm, CredentialsFormFallback } from "../_components/credentials-form";

export default function Signup() {
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="signup" />}>
      <CredentialsForm mode="signup" />
    </Suspense>
  );
}
