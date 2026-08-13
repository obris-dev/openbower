import { Suspense } from "react";
import { cookies } from "next/headers";

import { CredentialsForm, CredentialsFormFallback, REMEMBER_EMAIL_COOKIE, safeDecode } from "../_components/credentials-form";

export default async function Login() {
  // The remembered email lives in a cookie precisely so THIS server
  // render can paint the box filled and checked: localStorage would
  // force a post-hydration flip (a visible flash).
  const remembered = (await cookies()).get(REMEMBER_EMAIL_COOKIE)?.value ?? null;
  return (
    // useSearchParams (inside the form) needs a Suspense boundary;
    // nothing here is worth blocking pre-render on.
    <Suspense fallback={<CredentialsFormFallback mode="login" />}>
      <CredentialsForm mode="login" rememberedEmail={remembered === null ? null : safeDecode(remembered)} />
    </Suspense>
  );
}
