import { Suspense } from "react";
import { headers } from "next/headers";
import { credentialPostIsSameSite, resolveAuthBase } from "@bower/api";

import { CredentialsForm, CredentialsFormFallback } from "../_components/credentials-form";
import { HubSignIn } from "../_components/hub-sign-in";

export default async function Signup() {
  const host = (await headers()).get("host") ?? "";
  if (!credentialPostIsSameSite(host, resolveAuthBase())) return <HubSignIn mode="signup" />;
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="signup" />}>
      <CredentialsForm mode="signup" />
    </Suspense>
  );
}
