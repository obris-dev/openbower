import { Suspense } from "react";
import { cookies, headers } from "next/headers";
import { credentialPostIsSameSite, resolveAuthBase, resumeTargetsLoopback } from "@bower/api";

import { CredentialsForm, CredentialsFormFallback, REMEMBER_EMAIL_COOKIE, safeDecode } from "../_components/credentials-form";
import { HubSignIn } from "../_components/hub-sign-in";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function Login({ searchParams }: { searchParams: SearchParams }) {
  // Decided on the server from the request's host, so the first paint
  // is right and nothing flips after hydration.
  const host = (await headers()).get("host") ?? "";
  if (!credentialPostIsSameSite(host, resolveAuthBase())) return <HubSignIn mode="login" />;
  // The remembered email lives in a cookie precisely so THIS server
  // render can paint the box filled and checked: localStorage would
  // force a post-hydration flip (a visible flash).
  const remembered = (await cookies()).get(REMEMBER_EMAIL_COOKIE)?.value ?? null;
  // The person arrived from their own install, which sent them here
  // for the credentials; the card's one line says so (the address bar
  // is not theirs), decided here so the first paint carries it.
  const next = (await searchParams).next;
  const selfHost = resumeTargetsLoopback(typeof next === "string" ? next : null);
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="login" />}>
      <CredentialsForm
        mode="login"
        rememberedEmail={remembered === null ? null : safeDecode(remembered)}
        selfHost={selfHost}
      />
    </Suspense>
  );
}
