"use client";

import { CopyButton, Input, Label } from "@bower/ui";

/** A one-time value (a signing secret, an API key token) as the reveal
 * shows it: a labelled read-only field that selects whole on focus, the
 * copy button as the fast path, and the warning that it is shown once.
 * Shared by every reveal, so the ritual is one shape. */
export function SecretField({ id, label, value, note }: { id: string; label: string; value: string; note: string }) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <div className="mt-1 flex items-center gap-2">
        <Input
          id={id}
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="font-mono text-xs"
          autoFocus
        />
        <CopyButton value={value} />
      </div>
      <p className="mt-2 text-xs text-warning">{note}</p>
    </div>
  );
}
