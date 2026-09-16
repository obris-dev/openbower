// Client mirror of the server's create grammar (webhooks/serializers.py
// WebhookHeaderDef and DestinationCreateRequest, and the service's
// reserved-name guard): one fact, one module. Every bound the
// serializer refuses with a shape 400 (which the client cannot render)
// is caught here first, so the only refusals a user can reach are the
// envelope ones.

import {
  MAX_WEBHOOK_HEADERS,
  RESERVED_WEBHOOK_HEADER_NAMES,
  WEBHOOK_HEADER_NAME_GRAMMAR,
  WEBHOOK_HEADER_VALUE_GRAMMAR,
  WEBHOOK_URL_MAX_LENGTH,
} from "@bower/api";

export type HeaderRow = { name: string; value: string };
export type HeaderProblem = { index: number; message: string };

export const EMPTY_HEADER: HeaderRow = { name: "", value: "" };

const RESERVED = new Set(RESERVED_WEBHOOK_HEADER_NAMES);

/** Rows with both halves filled; a wholly blank row is not a header. */
export function contentfulHeaders(rows: readonly HeaderRow[]): HeaderRow[] {
  return rows.filter((row) => row.name.trim() !== "" || row.value !== "").map((row) => ({ name: row.name.trim(), value: row.value }));
}

/** The first row that cannot be sent, with its one-line cause; null
 * when every contentful row is well-formed. `index` is the row to mark. */
export function headersProblem(rows: readonly HeaderRow[]): HeaderProblem | null {
  const seen = new Set<string>();
  let contentful = 0;
  for (const [index, row] of rows.entries()) {
    const name = row.name.trim();
    if (name === "" && row.value === "") continue;
    contentful += 1;
    if (name === "") return { index, message: "Name this header." };
    if (!WEBHOOK_HEADER_NAME_GRAMMAR.test(name)) return { index, message: "Header names use letters, digits, and dashes." };
    if (RESERVED.has(name.toLowerCase())) return { index, message: `The ${name} header is set by every delivery.` };
    if (row.value === "") return { index, message: `Enter a value for ${name}.` };
    if (!WEBHOOK_HEADER_VALUE_GRAMMAR.test(row.value)) {
      return { index, message: "Header values use printable characters only, on one line." };
    }
    const folded = name.toLowerCase();
    if (seen.has(folded)) return { index, message: "Header names must be unique." };
    seen.add(folded);
  }
  if (contentful > MAX_WEBHOOK_HEADERS) {
    return { index: MAX_WEBHOOK_HEADERS, message: `A destination sends at most ${MAX_WEBHOOK_HEADERS} headers.` };
  }
  return null;
}

// The server's URL validator's host rule (Django's URLValidator): dotted
// labels of letters, digits, and dashes ending in an alphabetic
// top-level label (or a punycode one), or localhost, or an IP literal.
// new URL() is laxer (a bare word, an underscore), so the mirror
// checks the hostname it parsed. new URL() has already punycoded an
// international name, so the xn-- branch covers it.
const HOST_GRAMMAR =
  /^(?:localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-f:.]+\]|(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z-]{2,63}|xn--[a-z0-9]{1,59})\.?)$/i;

/** Why the URL cannot be sent, or null: the server's URLField plus its
 * http(s)-only rule, mirrored so the shape 400 is unreachable. */
export function urlProblem(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed === "") return "Enter the URL to deliver to.";
  if (trimmed.length > WEBHOOK_URL_MAX_LENGTH) return `URLs are capped at ${WEBHOOK_URL_MAX_LENGTH} characters.`;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return "Enter an http or https URL.";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "Enter an http or https URL.";
  if (!parsed.hostname) return "Enter an http or https URL.";
  if (!HOST_GRAMMAR.test(parsed.hostname)) return "Enter a full host name, like hooks.example.com.";
  return null;
}
