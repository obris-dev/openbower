"use client";

import { type FormEvent, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, ErrorMessage, Input, Label, PasswordInput } from "@bower/ui";
import { idpResumeUrl, webRoutes, withNext } from "@bower/api";

const MIN_PASSWORD = 8;

/** The cross-link under the form (login <-> signup); `href` is a
 * webRoutes path, and the in-flight ?next rides along automatically. */
export type CredentialsFooter = {
  prompt: string;
  label: string;
  href: string;
};

/** The shared credentials machinery behind login and signup: the web owns
 * the SCREEN, the IdP stays the authority `action` posts to. On success
 * the IdP session exists; with ?next we resume the in-flight OAuth
 * authorize, WITHOUT it (a direct visit, or a marketing-site link) there
 * is no flow to resume and idpResumeUrl's fallback would strand the
 * browser on the IdP's own pages, so we go to the app instead: its guard
 * starts a fresh OAuth round against the just-minted IdP session. */
export function CredentialsForm({
  action,
  submitLabel,
  busyLabel,
  passwordAutoComplete,
  confirmPassword = false,
  footer,
}: {
  action: (email: string, password: string) => Promise<string | null>;
  submitLabel: string;
  busyLabel: string;
  passwordAutoComplete: "current-password" | "new-password";
  confirmPassword?: boolean;
  footer: CredentialsFooter;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // `next` is read straight off the URL (no mount effect + state, which
  // would setState within an effect and cascade a render).
  const next = useSearchParams().get("next");

  const mismatch = confirmPassword && confirm.length > 0 && password !== confirm;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (confirmPassword && password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError(null);
    const failure = await action(email, password);
    if (failure) {
      setError(failure);
      setSubmitting(false);
      return;
    }
    window.location.href = next ? idpResumeUrl(next) : webRoutes.home;
  }

  return (
    <>
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
              autoComplete={passwordAutoComplete}
              required
              minLength={confirmPassword ? MIN_PASSWORD : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>
        {confirmPassword && (
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
        )}
        <Button type="submit" fullWidth loading={submitting} disabled={mismatch}>
          {submitting ? busyLabel : submitLabel}
        </Button>
      </form>
      <p className="mt-6 text-center text-sm text-ink/60 dark:text-paper/60">
        {footer.prompt}{" "}
        <a className="font-medium text-signal hover:underline" href={withNext(footer.href, next)}>
          {footer.label}
        </a>
      </p>
    </>
  );
}
