"use client";

import { useState, type FormEvent } from "react";
import { Button, Input } from "@bower/ui";

import { submitWaitlist } from "../_lib/waitlist";

// Email plus one button; on success the form swaps for the
// acknowledgement, which is the whole confirmation (nothing is sent).
// `source` names the form that converted (pricing, cta) and doubles as
// the id prefix, so two forms on one page never share an input id.

// The client's own copy (tier 3): the server's refusal body is not
// rendered, since a public form has nothing to diagnose per field.
const FAILED = "That did not go through. Try again in a moment.";

export function WaitlistForm({ source }: { source: string }) {
  const [email, setEmail] = useState("");
  const [joined, setJoined] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function join(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const result = await submitWaitlist(email.trim(), source);
      if (result.ok) setJoined(true);
      else setFailed(true);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  if (joined) {
    return (
      <div role="status" aria-live="polite" className="rounded-md border border-signal/40 bg-signal/10 px-4 py-3.5 text-left">
        <p className="text-base font-semibold text-ink dark:text-paper">You&rsquo;re on the list.</p>
        <p className="mt-1 text-sm text-ink/70 dark:text-paper/70">
          We&rsquo;ll reach <span className="font-medium text-ink dark:text-paper">{email.trim()}</span> when the hosted
          version opens.
        </p>
      </div>
    );
  }

  const errorId = `${source}-waitlist-error`;
  return (
    <form onSubmit={join} className="flex w-full flex-col gap-2.5 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1">
        <label htmlFor={`${source}-waitlist-email`} className="sr-only">
          Email address
        </label>
        <Input
          id={`${source}-waitlist-email`}
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@company.com"
          invalid={failed}
          aria-describedby={failed ? errorId : undefined}
        />
        {failed && (
          <p id={errorId} className="mt-1.5 text-xs text-danger">
            {FAILED}
          </p>
        )}
      </div>
      <Button type="submit" loading={busy} className="shrink-0">
        {busy ? "Joining…" : "Join the waitlist"}
      </Button>
    </form>
  );
}
