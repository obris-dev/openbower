// The sheet's grid and per-column cell styles, shared by the server
// rows and the client arriving row so a cell looks identical the
// instant it resolves.
export const GRID = "grid grid-cols-[0.9fr_1.2fr_1.1fr_1.3fr] gap-x-4 px-4";

export function filledClass(col: number): string {
  if (col === 0) return "block truncate text-ink/80 dark:text-paper/80";
  if (col === 1) return "block truncate tabular-nums text-ink/70 dark:text-paper/70";
  return "block truncate text-ink/50 underline decoration-ink/20 decoration-dotted underline-offset-2 dark:text-paper/50 dark:decoration-paper/20";
}
