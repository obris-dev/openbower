"use client";

import { type FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, Card, ErrorMessage, Input, Label, PasswordInput, ThemeToggle } from "@bower/ui";
import { idpLogin, idpResumeUrl, webRoutes, withNext } from "@bower/api";

// The web tier owns the login SCREEN; the IdP stays the authority. The OAuth
// authorize endpoint redirects unauthenticated browsers here with ?next= (an
// IdP-relative authorize path). Submitting posts credentials to the IdP's
// session API, then navigates back to `next` so the code flow completes.
function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // `next` is read straight off the URL (no mount effect + state, which would
  // setState within an effect and cascade a render).
  const next = useSearchParams().get("next");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const failure = await idpLogin(email, password);
    if (failure) {
      setError(failure);
      setSubmitting(false);
      return;
    }
    // With ?next we resume the in-flight authorize; WITHOUT it (a direct
    // visit, or the marketing site's links) there is no flow to resume,
    // and idpResumeUrl's fallback would strand the browser on the IdP's
    // own pages. Go to the app instead: its guard starts a fresh OAuth
    // round against the just-created IdP session and lands signed in.
    window.location.href = next ? idpResumeUrl(next) : webRoutes.home;
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm p-8">
        <h1 className="text-xl font-bold text-ink dark:text-paper">OpenBower</h1>
        <p className="mb-6 mt-1 text-sm text-ink/60 dark:text-paper/60">Sign in to continue.</p>
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          {error && <ErrorMessage message={error} />}
          <div>
            <Label htmlFor="email">Email</Label>
            <div className="mt-1.5">
              <Input
                id="email"
                type="email"
                autoComplete="email"
                autoFocus
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="password">Password</Label>
            <div className="mt-1.5">
              <PasswordInput
                id="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>
          <Button type="submit" fullWidth loading={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <p className="mt-6 text-center text-sm text-ink/60 dark:text-paper/60">
          Don&apos;t have an account?{" "}
          <a className="font-medium text-signal hover:underline" href={withNext(webRoutes.signup, next)}>
            Create account
          </a>
        </p>
      </Card>
    </main>
  );
}

export default function Login() {
  // useSearchParams needs a Suspense boundary (Next bails static rendering to
  // it); the login screen has no pre-render content worth blocking on.
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
