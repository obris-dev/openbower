import { Skeleton } from "@bower/ui";

import { ColumnHeads } from "./column-heads";

// The ghost table: the results' real column headers over placeholder
// rows, shared by the empty state (static, fading toward the invitation)
// and the searching state (pulsing, uniformly alive). One surface that
// goes awaiting -> filling -> filled.
// Fixed narrow bars for rank/size/score; the name and industry bars are
// fluid (w-full under a max) so the ghost compresses on narrow screens
// instead of overflowing the card.
const GHOST_WIDTHS = [
  ["w-6", "w-full max-w-40", "w-full max-w-24", "w-10", "w-8"],
  ["w-6", "w-full max-w-32", "w-full max-w-28", "w-10", "w-8"],
  ["w-6", "w-full max-w-36", "w-full max-w-20", "w-10", "w-8"],
  ["w-6", "w-full max-w-28", "w-full max-w-24", "w-10", "w-8"],
  ["w-6", "w-full max-w-44", "w-full max-w-16", "w-10", "w-8"],
];
const GHOST_FADE = ["opacity-70", "opacity-45", "opacity-20"];

export function GhostTable({ pulsing }: { pulsing: boolean }) {
  const rows = pulsing ? GHOST_WIDTHS : GHOST_WIDTHS.slice(0, GHOST_FADE.length);
  return (
    <table className="w-full text-left text-sm" aria-hidden>
      <ColumnHeads />
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className={pulsing ? undefined : GHOST_FADE[i]}>
            {row.map((width, j) => (
              <td key={j} className={j === 0 || j === row.length - 1 ? "py-3 pr-3" : "py-3 pr-4"}>
                <Skeleton
                  className={`h-3 ${pulsing ? "" : "animate-none"} ${width} ${j === 0 || j === row.length - 1 ? "ml-auto" : ""}`}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
