const ROWS = [
  ["acme.com", "Acme", "Fleet routing", "0.94", "Fleet software"],
  ["globex.com", "Globex", "Freight visibility", "0.93", "Freight ops"],
  ["initech.io", "Initech", "Dispatch automation", "0.91", "Fleet software"],
  ["contoso.com", "Contoso", "Load matching", "0.89", "Freight ops"],
  ["northwind.com", "Northwind", "Yard management", "0.88", "Freight ops"],
];

/** A stylized, honest render of the product's list grid (CSS, no
 * screenshot to go stale): ranked look-alikes with scores and groups. */
export function ProductPreview() {
  return (
    <section className="reveal px-6 pb-24">
      <div className="mx-auto max-w-3xl overflow-hidden rounded-xl border border-ink/10 bg-paper shadow-2xl shadow-black/10 dark:border-paper/10 dark:bg-ink dark:shadow-black/30">
        <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3 dark:border-paper/10">
          <p className="text-sm font-semibold text-ink dark:text-paper">Logistics software prospects</p>
          <span className="rounded-md bg-signal/10 px-2 py-1 text-xs font-medium text-signal">
            2,005 matches to the inflection point
          </span>
        </div>
        <div className="grid grid-cols-[1.2fr_0.9fr_1.1fr_0.5fr_1fr] gap-x-4 border-b border-ink/10 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-ink/40 dark:border-paper/10 dark:text-paper/40">
          <span>Domain</span>
          <span>Name</span>
          <span>What the site says</span>
          <span className="text-right">Score</span>
          <span>Group</span>
        </div>
        {ROWS.map((row) => (
          <div
            key={row[0]}
            className="grid grid-cols-[1.2fr_0.9fr_1.1fr_0.5fr_1fr] gap-x-4 border-b border-ink/5 px-4 py-2.5 text-sm last:border-0 dark:border-paper/5"
          >
            <span className="truncate font-medium text-signal">{row[0]}</span>
            <span className="truncate text-ink/80 dark:text-paper/80">{row[1]}</span>
            <span className="truncate text-ink/50 dark:text-paper/50">{row[2]}</span>
            <span className="text-right tabular-nums text-ink/70 dark:text-paper/70">{row[3]}</span>
            <span className="truncate text-ink/50 dark:text-paper/50">{row[4]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
