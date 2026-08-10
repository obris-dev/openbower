"use client";

import { type FormEvent, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, ErrorMessage, FieldError, Input, Label, PasswordInput } from "@bower/ui";
import { idpLogin, idpResumeUrl, idpSignup, webRoutes, withNext } from "@bower/api";

const MIN_PASSWORD = 8;

/** The cross-link under the form (login <-> signup); `href` is a
 * webRoutes path, and the in-flight ?next rides along automatically. */
export type CredentialsFooter = {
  prompt: string;
  label: string;
  href: string;
};

const REMEMBER_EMAIL_KEY = "bower.login.email";

type Mode = {
  subtitle: string;
  action: (email: string, password: string) => Promise<string | null>;
  submitLabel: string;
  busyLabel: string;
  passwordAutoComplete: "current-password" | "new-password";
  confirmPassword: boolean;
  rememberEmail: boolean;
  footer: CredentialsFooter;
};

// The per-screen config lives WITH the component (pages are server
// components and cannot pass the action function across the boundary;
// they name a mode instead). A future forgot-password screen is another
// entry here plus its route.
const MODES: Record<"login" | "signup", Mode> = {
  login: {
    subtitle: "Sign in to continue.",
    action: idpLogin,
    submitLabel: "Sign in",
    busyLabel: "Signing in…",
    passwordAutoComplete: "current-password",
    confirmPassword: false,
    rememberEmail: true,
    footer: { prompt: "Don't have an account?", label: "Create account", href: webRoutes.signup },
  },
  signup: {
    subtitle: "Create your account.",
    action: idpSignup,
    submitLabel: "Create account",
    busyLabel: "Creating account…",
    passwordAutoComplete: "new-password",
    confirmPassword: true,
    rememberEmail: false,
    footer: { prompt: "Already have an account?", label: "Sign in", href: webRoutes.login },
  },
};

/** The shared credentials machinery behind login and signup: the web owns
 * the SCREEN, the IdP stays the authority `action` posts to. On success
 * the IdP session exists; with ?next we resume the in-flight OAuth
 * authorize, WITHOUT it (a direct visit, or a marketing-site link) there
 * is no flow to resume and idpResumeUrl's fallback would strand the
 * browser on the IdP's own pages, so we go to the app instead: its guard
 * starts a fresh OAuth round against the just-minted IdP session. */
export function CredentialsForm({ mode }: { mode: "login" | "signup" }) {
  const { subtitle, action, submitLabel, busyLabel, passwordAutoComplete, confirmPassword, rememberEmail, footer } =
    MODES[mode];
  // Lazy init is safe: the Suspense boundary (useSearchParams) means this
  // form never server-renders, so there is no hydration to mismatch.
  const [email, setEmail] = useState(() =>
    rememberEmail && typeof window !== "undefined" ? (window.localStorage.getItem(REMEMBER_EMAIL_KEY) ?? "") : "",
  );
  const [remember, setRemember] = useState(() =>
    rememberEmail && typeof window !== "undefined" ? window.localStorage.getItem(REMEMBER_EMAIL_KEY) !== null : false,
  );
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
    if (rememberEmail) {
      try {
        if (remember) window.localStorage.setItem(REMEMBER_EMAIL_KEY, email);
        else window.localStorage.removeItem(REMEMBER_EMAIL_KEY);
      } catch {
        // Preference just doesn't persist (private mode etc.).
      }
    }
    window.location.href = next ? idpResumeUrl(next) : webRoutes.home;
  }

  return (
    <>
      <p className="mb-6 mt-1 text-sm text-ink/60 dark:text-paper/60">{subtitle}</p>
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
        {rememberEmail && (
          <label className="flex items-center gap-2 text-sm text-ink/70 dark:text-paper/70">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="accent-signal"
            />
            Remember my email
          </label>
        )}
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
            {mismatch && <FieldError id="confirm-error">Passwords do not match.</FieldError>}
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
