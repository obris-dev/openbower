import type { ColumnType, IngestColumn } from "@bower/api";

/** A value per column type that the server's shape rule accepts, so
 * the pasted command is accepted as is. Mirrors the shape rules the
 * contract's cell_types module enforces (number, currency and date
 * carry rules; text, url and email pass through). Keyed by the
 * generated type, so a type added server-side is a compile error here
 * rather than a sample its rule may refuse. */
const SAMPLE_VALUE: Record<ColumnType, string> = {
  text: "Acme",
  number: "42",
  currency: "1200",
  date: "2026-01-15",
  url: "https://acme.com",
  email: "hello@acme.com",
};

export function sampleValue(column: IngestColumn): string {
  return SAMPLE_VALUE[column.type];
}

// The sample row's external_id: a sender puts its own id for the row here.
// The envelope's keys mirror the server's (apps/core/sheets/rows.py).
const SAMPLE_EXTERNAL_ID = "your-id-for-this-row";

/** One command that runs as pasted once OPENBOWER_API_KEY is set:
 * every settable column with a sample value of its type, so the shape
 * is copied rather than guessed. The row is an envelope,
 * {external_id, cells}. The body is single-quoted for the shell, with
 * any quote inside it escaped the POSIX way. */
export function curlFor(url: string, columns: IngestColumn[]): string {
  const cells = Object.fromEntries(columns.map((column) => [column.key, sampleValue(column)]));
  const row = { external_id: SAMPLE_EXTERNAL_ID, cells };
  const body = JSON.stringify({ rows: [row] }, null, 2).replace(/'/g, "'\\''");
  return [
    `curl -X POST ${url} \\`,
    `  -H "Authorization: Bearer $OPENBOWER_API_KEY" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '${body}'`,
  ].join("\n");
}
