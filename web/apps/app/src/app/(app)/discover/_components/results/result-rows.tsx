import type { LookalikeListResponse } from "@bower/api";

import { ColumnHeads } from "./column-heads";

/** The shared result-rows table (used whole, or per group section). */
export function ResultRows({ items }: { items: LookalikeListResponse["items"] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <ColumnHeads />
        <tbody className="divide-y divide-hairline">
          {items.map((item) => (
            // Composite key: rank is unique per result set and company.id
            // per company, so neither alone can collide.
            <tr key={`${item.rank}-${item.company.id}`} className="align-top">
              <td className="py-2.5 pr-3 text-right tabular-nums text-faint">{item.rank}</td>
              <td className="max-w-52 py-2.5 pr-4">
                <span className="block truncate font-medium text-foreground">
                  {item.company.name || item.company.domain}
                </span>
                <a
                  href={`https://${item.company.domain}`}
                  target="_blank"
                  rel="noreferrer"
                  title={item.description || undefined}
                  className="block truncate text-xs text-muted hover:text-signal"
                >
                  {item.company.domain}
                </a>
              </td>
              <td className="max-w-40 truncate py-2.5 pr-4 text-xs text-muted">
                {item.company.industry}
              </td>
              <td className="py-2.5 pr-4 text-xs tabular-nums text-muted">
                {item.company.size_band}
              </td>
              <td className="py-2.5 text-right tabular-nums text-muted">{item.score.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
