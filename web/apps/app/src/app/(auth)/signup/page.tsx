import { Suspense } from "react";
import { headers } from "next/headers";
import { credentialPostIsSameSite, resolveAuthBase, resumeTargetsLoopback } from "@bower/api";

import { CredentialsForm, CredentialsFormFallback } from "../_components/credentials-form";
import { HubSignIn } from "../_components/hub-sign-in";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function Signup({ searchParams }: { searchParams: SearchParams }) {
  const host = (await headers()).get("host") ?? "";
  if (!credentialPostIsSameSite(host, resolveAuthBase())) return <HubSignIn mode="signup" />;
  // As on the login page: the one line under the brand says the person
  // came from their own install, decided here for the first paint.
  const next = (await searchParams).next;
  const selfHost = resumeTargetsLoopback(typeof next === "string" ? next : null);
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="signup" />}>
      <CredentialsForm mode="signup" selfHost={selfHost} />
    </Suspense>
  );
}
