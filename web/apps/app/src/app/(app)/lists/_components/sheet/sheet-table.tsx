import { isNumericColumn, type ListColumn, type ListRowWire } from "@bower/api";

function Cell({ column, value }: { column: ListColumn; value: string }) {
  if (!value) return null;
  if (column.type === "url") {
    const href = /^https?:\/\//.test(value) ? value : `https://${value}`;
    return (
      <a href={href} target="_blank" rel="noreferrer" className="text-muted hover:text-signal">
        {value}
      </a>
    );
  }
  if (column.type === "email") {
    return (
      <a href={`mailto:${value}`} className="text-muted hover:text-signal">
        {value}
      </a>
    );
  }
  return <span className="text-foreground">{value}</span>;
}

/** The rows table: columns straight from the sheet's schema (types drive
 * rendering only), a bounded scroll region so a long sheet never owns
 * the page scroll. */
export function SheetTable({ columns, rows }: { columns: ListColumn[]; rows: ListRowWire[] }) {
  if (rows.length === 0) {
    return <p className="p-6 text-sm text-muted">This sheet has no rows.</p>;
  }
  return (
    <div className="max-h-[70vh] overflow-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-faint">
            <th className="sticky top-0 bg-surface px-4 py-3 text-right font-medium">#</th>
            {columns.map((column) => (
              <th
                key={column.key}
                title={column.label}
                className={`sticky top-0 bg-surface px-4 py-3 font-medium ${isNumericColumn(column) ? "text-right" : ""}`}
              >
                {/* Truncation needs a BLOCK: max-w on a table cell is
                    inert under auto table layout. */}
                <span className="block max-w-64 truncate">{column.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {rows.map((row) => (
            <tr key={row.id} className="align-top">
              <td className="px-4 py-2.5 text-right tabular-nums text-faint">{row.position}</td>
              {columns.map((column) => (
                <td key={column.key} className={`px-4 py-2.5 ${isNumericColumn(column) ? "text-right tabular-nums" : ""}`}>
                  <div className="max-w-64 truncate">
                    <Cell column={column} value={row.data[column.key] ?? ""} />
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
