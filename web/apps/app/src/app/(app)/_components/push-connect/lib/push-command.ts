import type { ColumnType, IngestColumn } from "@bower/api";

/** Values per column type that the server's shape rule accepts, so a
 * pasted command is accepted as is. Mirrors the shape rules the
 * contract's cell_types module enforces (number, currency, date and
 * datetime carry rules; text, url and email pass through). Keyed by the
 * generated type, so a type added server-side is a compile error here
 * rather than a sample its rule may refuse. The first of each is the
 * one a column's example shows. */
const SAMPLE_VALUES: Record<ColumnType, readonly string[]> = {
  text: ["Acme", "Globex", "Initech", "Hooli", "Umbrella"],
  number: ["42", "7", "128", "3"],
  currency: ["1200", "4500", "99", "25000"],
  date: ["2026-01-15", "2026-03-02", "2025-11-20", "2026-06-30"],
  datetime: ["2026-01-15T09:30:00Z", "2026-03-02T14:05:00Z", "2025-11-20T18:45:00Z", "2026-06-30T07:15:00Z"],
  url: ["https://acme.com", "https://globex.io", "https://initech.example", "https://hooli.example"],
  email: ["hello@acme.com", "team@globex.io", "ops@initech.example", "sales@hooli.example"],
};

/** A whole number below `bound`: how a sample picks, injectable so a
 * test can pin it. */
export type Pick = (bound: number) => number;
export const randomPick: Pick = (bound) => Math.floor(Math.random() * bound);

export function sampleValue(column: IngestColumn, pick: Pick = () => 0): string {
  const values = SAMPLE_VALUES[column.type];
  return values[pick(values.length)] ?? values[0]!;
}

const ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";
const ID_LENGTH = 8;

function sampleId(prefix: string, pick: Pick): string {
  const chars = Array.from({ length: ID_LENGTH }, () => ID_ALPHABET[pick(ID_ALPHABET.length)]);
  return `${prefix}_${chars.join("")}`;
}

/** One sample row in the push envelope (apps/core/sheets/rows.py): a
 * fresh external_id, so a second run of the command lands a second row
 * rather than being skipped as a resend, and a value per settable column. */
export type SampleRow = { external_id: string; cells: Record<string, string> };

export function sampleRow(columns: IngestColumn[], pick: Pick = randomPick): SampleRow {
  const cells = Object.fromEntries(columns.map((column) => [column.key, sampleValue(column, pick)]));
  return { external_id: sampleId("row", pick), cells };
}

/** A sample push: a fresh request_id (the request's idempotency key, so a
 * retry of this very command lands once) and one sample row. */
export type SamplePush = { request_id: string; rows: SampleRow[] };

export function samplePush(columns: IngestColumn[], pick: Pick = randomPick): SamplePush {
  return { request_id: sampleId("req", pick), rows: [sampleRow(columns, pick)] };
}

/** How a push proves itself: an account API key in a header (a sheet's
 * ingest), or nothing beside the URL, whose token is the credential (a
 * custom signal's webhook URL). */
export const PUSH_CREDENTIAL = { API_KEY: "api-key", IN_URL: "in-url" } as const;
export type PushCredential = (typeof PUSH_CREDENTIAL)[keyof typeof PUSH_CREDENTIAL];

/** One command that runs as pasted (once OPENBOWER_API_KEY is set, for an
 * API key push), sending `push`. The body is single-quoted for the shell,
 * with any quote inside it escaped the POSIX way. */
export function curlFor(url: string, push: SamplePush, credential: PushCredential): string {
  const body = JSON.stringify(push, null, 2).replace(/'/g, "'\\''");
  const lines = [`curl -X POST ${url} \\`];
  if (credential === PUSH_CREDENTIAL.API_KEY) lines.push(`  -H "Authorization: Bearer $OPENBOWER_API_KEY" \\`);
  lines.push(`  -H "Content-Type: application/json" \\`, `  -d '${body}'`);
  return lines.join("\n");
}
