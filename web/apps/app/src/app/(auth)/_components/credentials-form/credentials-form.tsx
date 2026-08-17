"use client";

import { type FormEvent, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, ErrorMessage, FieldError, Input, Label, PasswordInput } from "@bower/ui";
import { type CredentialFailure, idpResumeUrl, webRoutes, withNext } from "@bower/api";

import { REMEMBER_EMAIL_COOKIE, REMEMBER_MAX_AGE_SECONDS } from "./constants";
import { MODES } from "./modes";
import type { ModeName } from "./types";

const MIN_PASSWORD = 8;

function writeRememberCookie(email: string | null): void {
  const secure = window.location.protocol === "https:" ? "; secure" : "";
  document.cookie =
    email === null
      ? `${REMEMBER_EMAIL_COOKIE}=; path=/; max-age=0; samesite=lax${secure}`
      : `${REMEMBER_EMAIL_COOKIE}=${encodeURIComponent(email)}; path=/; max-age=${REMEMBER_MAX_AGE_SECONDS}; samesite=lax${secure}`;
}

/** The shared credentials machinery behind login and signup: the web owns
 * the SCREEN, the IdP stays the authority `action` posts to. On success
 * the IdP session exists; with ?next we resume the in-flight OAuth
 * authorize, WITHOUT it (a direct visit, or a marketing-site link) there
 * is no flow to resume and idpResumeUrl's fallback would strand the
 * browser on the IdP's own pages, so we go to the app instead: its guard
 * starts a fresh OAuth round against the just-minted IdP session. */
export function CredentialsForm({
  mode,
  rememberedEmail = null,
}: {
  mode: ModeName;
  rememberedEmail?: string | null;
}) {
  const { subtitle, action, submitLabel, busyLabel, passwordAutoComplete, confirmPassword, rememberEmail, footer } =
    MODES[mode];
  // Untouched (null) falls back to the SERVER-read cookie value / its
  // presence, so the remembered state is correct in the very first
  // paint; the first keystroke or click takes over.
  const [emailInput, setEmailInput] = useState<string | null>(null);
  const [rememberInput, setRememberInput] = useState<boolean | null>(null);
  const email = emailInput ?? rememberedEmail ?? "";
  const remember = rememberInput ?? rememberedEmail !== null;
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<CredentialFailure | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // `next` is read straight off the URL (no mount effect + state, which
  // would setState within an effect and cascade a render).
  const next = useSearchParams().get("next");

  const mismatch = confirmPassword && confirm.length > 0 && password !== confirm;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (confirmPassword && password !== confirm) {
      setError({ message: "Passwords do not match." });
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
      writeRememberCookie(remember ? email : null);
    }
    window.location.href = next ? idpResumeUrl(next) : webRoutes.home;
  }

  return (
    <>
      <p className="mb-6 mt-1 text-sm text-muted">{subtitle}</p>
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error?.message && <ErrorMessage message={error.message} />}
        <div>
          <Label htmlFor="email">Email</Label>
          <div className="mt-1.5">
            <Input
              id="email"
              type="email"
              autoComplete="email"
              autoFocus
              required
              invalid={Boolean(error?.fields?.email)}
              aria-describedby={error?.fields?.email ? "email-error" : undefined}
              value={email}
              onChange={(e) => setEmailInput(e.target.value)}
            />
          </div>
          {error?.fields?.email && <FieldError id="email-error">{error.fields.email.join(" ")}</FieldError>}
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <div className="mt-1.5">
            <PasswordInput
              id="password"
              autoComplete={passwordAutoComplete}
              required
              minLength={confirmPassword ? MIN_PASSWORD : undefined}
              invalid={Boolean(error?.fields?.password)}
              aria-describedby={error?.fields?.password ? "password-error" : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error?.fields?.password && <FieldError id="password-error">{error.fields.password.join(" ")}</FieldError>}
        </div>
        {rememberEmail && (
          <label className="flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRememberInput(e.target.checked)}
              className="accent-signal"
            />
            Remember me
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
      <p className="mt-6 text-center text-sm text-muted">
        {footer.prompt}{" "}
        <a className="font-medium text-signal hover:underline" href={withNext(footer.href, next)}>
          {footer.label}
        </a>
      </p>
    </>
  );
}

