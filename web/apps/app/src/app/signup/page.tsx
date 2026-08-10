"use client";

import { type FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, Card, ErrorMessage, Input, Label, PasswordInput, ThemeToggle } from "@bower/ui";
import { idpResumeUrl, idpSignup, webRoutes, withNext } from "@bower/api";

const MIN_PASSWORD = 8;

// Self-serve account creation. Mirrors the login page: the web owns the
// screen, the IdP is the authority. On success the IdP mints the session,
// then we resume the OAuth authorize flow via ?next=, exactly like login.
function SignupForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // `next` is read straight off the URL (no mount effect + state, which would
  // setState within an effect and cascade a render).
  const next = useSearchParams().get("next");

  const mismatch = confirm.length > 0 && password !== confirm;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    const failure = await idpSignup(email, password);
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
        <p className="mb-6 mt-1 text-sm text-ink/60 dark:text-paper/60">Create your account.</p>
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
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="confirm">Confirm password</Label>
            <div className="mt-1.5">
              <PasswordInput
                id="confirm"
                autoComplete="new-password"
                required
                invalid={mismatch}
                aria-describedby={mismatch ? "confirm-error" : undefined}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>
            {mismatch && (
              <p id="confirm-error" className="mt-1 text-xs text-red-600 dark:text-red-400">
                Passwords do not match.
              </p>
            )}
          </div>
          <Button type="submit" fullWidth loading={submitting} disabled={mismatch}>
            {submitting ? "Creating account…" : "Create account"}
          </Button>
        </form>
        <p className="mt-6 text-center text-sm text-ink/60 dark:text-paper/60">
          Already have an account?{" "}
          <a className="font-medium text-signal hover:underline" href={withNext(webRoutes.login, next)}>
            Sign in
          </a>
        </p>
      </Card>
    </main>
  );
}

export default function Signup() {
  // useSearchParams needs a Suspense boundary (Next bails static rendering to
  // it); the signup screen has no pre-render content worth blocking on.
  return (
    <Suspense>
      <SignupForm />
    </Suspense>
  );
}
