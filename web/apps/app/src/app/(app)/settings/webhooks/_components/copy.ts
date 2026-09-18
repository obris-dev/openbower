// The family's copy in one place: the delete ritual, the secret's two
// lines, the empty state, and the verify guide (which the reveal panel
// and the detail page both render). Protocol names come off the
// contract, never retyped.

import {
  WEBHOOK_ID_HEADER,
  WEBHOOK_ROTATION_GRACE_SECONDS,
  WEBHOOK_SECRET_PREFIX,
  WEBHOOK_SIGNATURE_HEADER,
  WEBHOOK_SIGNATURE_VERSION,
  WEBHOOK_TIMESTAMP_HEADER,
} from "@bower/api";

export const DELETE_DESTINATION_QUESTION = "Delete this destination?";
export const DELETE_DESTINATION_CONSEQUENCE = "Its delivery log goes with it.";

// The grace window, as the hours a person reads it in (the contract
// holds seconds).
export const ROTATION_GRACE_HOURS = Math.round(WEBHOOK_ROTATION_GRACE_SECONDS / 3600);
export const SECRET_REVEAL_NOTE =
  "This is the only time this secret will be shown, so copy it somewhere safe. If you lose it, rotate it from this destination's page.";
export const SECRET_RECOVERY_NOTE = `The signing secret was shown once, when this destination was created. If you have lost it, rotate it: the new secret shows once, and the old one keeps working for ${ROTATION_GRACE_HOURS} hours so your receiver can switch.`;
export const ROTATE_SECRET = "Rotate secret";
export const ROTATE_QUESTION = "Rotate the signing secret?";
export const ROTATE_CONSEQUENCE = `The old secret keeps working for ${ROTATION_GRACE_HOURS} hours, then stops.`;
export const SECRET_ROTATED_NOTE = `This is the only time this secret will be shown, so copy it somewhere safe. Update your receiver within ${ROTATION_GRACE_HOURS} hours, while the old secret still works.`;
export function rotatedLede(label: string): string {
  return `${label} has a new signing secret.`;
}
export const DONE = "Done";
/** "Used by 1 column" | "Used by N columns" | null when none. */
export function usageLine(count: number): string | null {
  if (count <= 0) return null;
  return count === 1 ? "Used by 1 column" : `Used by ${count} columns`;
}

// The verify guide: what a receiver gets and how it checks it. The
// headers and the recipe are the Standard Webhooks scheme, so a
// library for it accepts the secret as is.
export const VERIFY_INTRO = "Each delivery is a JSON POST with three headers:";
export const VERIFY_BODY =
  "The body carries `type` (ping or digest) and `test`. A delivery with `test: true` came from a Test button: verify it, then ignore it.";
export const VERIFY_HEADERS: { name: string; meaning: string }[] = [
  { name: WEBHOOK_ID_HEADER, meaning: "unique per delivery" },
  { name: WEBHOOK_TIMESTAMP_HEADER, meaning: "unix seconds" },
  { name: WEBHOOK_SIGNATURE_HEADER, meaning: `"${WEBHOOK_SIGNATURE_VERSION}," then the signature` },
];
// The headers and the recipe follow a published format with libraries
// in most languages; the guide links to it so nobody has to hand-roll
// the check.
export const STANDARD_WEBHOOKS_SPEC_URL =
  "https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md";
export const STANDARD_WEBHOOKS_URL = "https://github.com/standard-webhooks/standard-webhooks";
export const VERIFY_SPEC_LEAD = "Follows the ";
export const VERIFY_SPEC_LINK = "Standard Webhooks spec";
export const VERIFY_LIBRARY_LEAD = ". Hand-roll or use ";
export const VERIFY_LIBRARY_LINK = "available libraries";
export const VERIFY_LIBRARY_TAIL = " for verification.";
export const VERIFY_EXAMPLE = `key = base64.b64decode(secret.removeprefix("${WEBHOOK_SECRET_PREFIX}"))
signed = f"{headers['${WEBHOOK_ID_HEADER}']}.{headers['${WEBHOOK_TIMESTAMP_HEADER}']}.".encode() + body
digest = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
valid = hmac.compare_digest("${WEBHOOK_SIGNATURE_VERSION}," + digest, headers["${WEBHOOK_SIGNATURE_HEADER}"])`;

export const EMPTY_TITLE = "No destinations yet";
export const EMPTY_SUBTITLE =
  "A destination is a URL that receives signed deliveries: add one, send it a test, then point sheets at it.";

export const HEADERS_NOTE = "Sent with every delivery. Values are kept encrypted and never shown again.";
