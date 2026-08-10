import { Suspense } from "react";

import { CredentialsForm } from "../_components/credentials-form";

export default function Signup() {
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense>
      <CredentialsForm mode="signup" />
    </Suspense>
  );
}
