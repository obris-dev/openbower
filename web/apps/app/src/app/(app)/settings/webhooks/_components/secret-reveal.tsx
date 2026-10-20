"use client";

import { SecretField } from "../../../_components/secret-field";
import { SECRET_REVEAL_NOTE } from "./copy";
import { VerifyGuide } from "./verify-guide";

const SECRET_ID = "destination-signing-secret";

/** The one-time panel: a signing secret the create or rotate response
 * carried, shown here and nowhere else again, and (after a create) how
 * a receiver uses it. Selecting the field selects the whole value; the
 * copy button is the fast path. */
export function SecretReveal({
  label,
  secret,
  lede,
  note = SECRET_REVEAL_NOTE,
  guide = true,
}: {
  label: string;
  secret: string;
  lede?: string;
  note?: string;
  guide?: boolean;
}) {
  return (
    <div className="space-y-6">
      <p className="text-sm text-foreground">
        {lede ?? (
          <>
            <span className="font-medium">{label}</span> is ready to receive deliveries.
          </>
        )}
      </p>
      <SecretField id={SECRET_ID} label="Signing secret" value={secret} note={note} />
      {guide && <VerifyGuide />}
    </div>
  );
}
