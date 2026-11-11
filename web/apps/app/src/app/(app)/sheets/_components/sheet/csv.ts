// Shared CSV assembly: files are built CLIENT-SIDE from paged JSON (the
// house rule; no server CSV surface exists).

/** RFC 4180 quoting (comma, quote, or either newline char is wrapped,
 * inner quotes doubled), plus a formula guard: text starting with = + -
 * @ would EXECUTE when the CSV opens in a spreadsheet, so string values
 * get a leading apostrophe. `numeric` columns skip the guard ONLY for
 * values that actually look like numbers ("-5" must sum in a
 * spreadsheet); column types are display-only, so junk in a number
 * column stays guarded. */
export function csvField(value: string | number, opts?: { numeric?: boolean }): string {
  let s = String(value);
  const plainNumber = opts?.numeric && /^[+-]?[\d,]*\.?\d+$/.test(s);
  if (typeof value === "string" && !plainNumber && /^[=+\-@]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Hand the built text to the browser as a download. */
export function saveCsvFile(filename: string, text: string): void {
  // The BOM: Excel assumes a legacy codepage for BOM-less UTF-8.
  const url = URL.createObjectURL(new Blob(["\ufeff", text], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // Revoke on the next tick: a synchronous revoke can race the browser
  // starting the download from the blob URL.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
