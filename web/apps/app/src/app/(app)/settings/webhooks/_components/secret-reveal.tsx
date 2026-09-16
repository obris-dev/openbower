"use client";

import { CopyButton, Input, Label } from "@bower/ui";

import { SECRET_REVEAL_NOTE } from "./copy";
import { VerifyGuide } from "./verify-guide";

const SECRET_ID = "destination-signing-secret";

/** The one-time panel: the signing secret the create response carried,
 * shown here and nowhere else again, and how a receiver uses it.
 * Selecting the field selects the whole value; the copy button is the
 * fast path. */
export function SecretReveal({ label, secret }: { label: string; secret: string }) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-foreground">
        <span className="font-medium">{label}</span> is ready to receive deliveries.
      </p>
      <div>
        <Label htmlFor={SECRET_ID}>Signing secret</Label>
        <div className="mt-1 flex items-center gap-2">
          <Input
            id={SECRET_ID}
            readOnly
            value={secret}
            onFocus={(e) => e.currentTarget.select()}
            className="font-mono text-xs"
            autoFocus
          />
          <CopyButton value={secret} />
        </div>
        <p className="mt-2 text-xs text-warning">{SECRET_REVEAL_NOTE}</p>
      </div>
      <VerifyGuide />
    </div>
  );
}
