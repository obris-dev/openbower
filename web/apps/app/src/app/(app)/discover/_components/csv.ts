// Client-side CSV assembly for the lead-list download: the file is built
// HERE from the same paged JSON the results views read, so the server
// has no CSV surface at all (one request path, one data shape).

import type { LookalikeItem } from "@bower/api";

const HEADER = ["rank", "score", "group", "domain", "name", "industry", "locality", "region", "country"];

// RFC 4180 quoting (comma, quote, or EITHER newline char is wrapped,
// inner quotes doubled), plus a formula guard: crawled text starting
// with = + - @ would EXECUTE when the CSV opens in a spreadsheet, so
// string values get a leading apostrophe (the standard defusal; numeric
// columns are never prefixed).
function field(value: string | number): string {
  let s = String(value);
  if (typeof value === "string" && /^[=+\-@]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function lookalikeCsv(items: LookalikeItem[]): string {
  const lines = [HEADER.join(",")];
  for (const item of items) {
    lines.push(
      [
        item.rank,
        item.score,
        item.group,
        item.company.domain,
        item.company.name,
        item.company.industry,
        item.company.locality,
        item.company.region,
        item.company.country,
      ]
        .map(field)
        .join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

/** Hand the built text to the browser as a download. */
export function saveCsv(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // Revoke on the next tick: a synchronous revoke can race the browser
  // starting the download from the blob URL.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
