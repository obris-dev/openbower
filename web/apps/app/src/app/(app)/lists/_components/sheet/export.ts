import { fetchListRows, isNumericColumn, ROWS_PAGE_LIMIT, type ListSummary } from "@bower/api";

import { csvField, saveCsvFile } from "./csv";
import { exportableColumns } from "./lib/exportable-columns";

/** Build the sheet's CSV client-side from the same rows pages the table
 * reads (the house rule: no server CSV surface). Pages in sheet order via
 * the server's own cursors. */
export async function downloadSheetCsv(
  detail: ListSummary,
  { onUnauthenticated }: { onUnauthenticated: () => void },
): Promise<void> {
  // A webhook column holds no row data, so it is not a column of the file.
  const columns = exportableColumns(detail.columns);
  const lines = [columns.map((c) => csvField(c.label)).join(",")];
  let cursor: string | undefined;
  for (;;) {
    const res = await fetchListRows(detail.id, { after: cursor, limit: ROWS_PAGE_LIMIT });
    if (res.status === "unauthenticated") {
      onUnauthenticated();
      return;
    }
    if (res.status !== "ok") throw new Error(res.message);
    for (const row of res.data.items) {
      lines.push(columns.map((c) => csvField(row.data[c.key] ?? "", { numeric: isNumericColumn(c) })).join(","));
    }
    if (!res.data.next_cursor) break;
    // A cursor that fails to advance would loop forever; today's server
    // strictly advances, this is the belt.
    if (res.data.next_cursor === cursor) throw new Error("export cursor did not advance");
    cursor = res.data.next_cursor;
  }
  const slug = detail.label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "list";
  saveCsvFile(`${slug}.csv`, lines.join("\r\n") + "\r\n");
}
