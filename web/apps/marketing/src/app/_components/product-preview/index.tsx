import { ArrivalDemo } from "./ArrivalDemo";
import { GRID, filledClass } from "./cells";

// The sheet the arriving row lands on: settled rows, server markup.
const FILLED_ROWS: [string, string, string, string][] = [
  ["acme.com", "Dana Wells, VP Sales", "$18M Series A, Mar 2026", "Opened an Austin office"],
  ["globex.com", "Priya Shah, Head of Growth", "$42M Series B, Jan 2026", "Launched EU data residency"],
  ["initech.io", "Marcus Cole, COO", "Seed, Aug 2025", "Hiring its first sales team"],
  ["contoso.com", "Elena Ruiz, Founder", "$7M Seed, Nov 2025", "Shipped SOC 2 Type II"],
];

// The shimmer sweep, a light glint over a muted skeleton (legible on
// both themes). Reduced motion is already covered by the paused clock
// (no cell ever enters the shimmer state); the media query is a floor
// for any shimmer that renders anyway.
const SHIMMER_CSS = `
.ob-shimmer{background:linear-gradient(90deg,transparent,rgba(255,255,255,0.35),transparent);transform:translateX(-100%);animation:ob-shimmer 1.3s ease-in-out infinite;}
@keyframes ob-shimmer{to{transform:translateX(100%);}}
@media (prefers-reduced-motion: reduce){.ob-shimmer{animation:none;opacity:0;}}
`;

/** A stylized, honest render of a pushed row researching itself (CSS
 * and a timeline, no screen recording to go stale). The card and its
 * settled rows are server markup; only the arriving row and the badge
 * it drives are a client leaf (ArrivalDemo). */
export function ProductPreview() {
  return (
    <section className="reveal px-6 pb-24">
      <style>{SHIMMER_CSS}</style>
      <div className="mx-auto max-w-3xl overflow-hidden rounded-xl border border-ink/10 bg-paper shadow-2xl shadow-black/10 dark:border-paper/10 dark:bg-ink dark:shadow-black/30">
        <ArrivalDemo />
        {FILLED_ROWS.map(([domain, contact, fundraise, headline]) => (
          <div key={domain} className={`${GRID} border-b border-ink/5 py-2.5 text-sm last:border-0 dark:border-paper/5`}>
            <span className="truncate font-medium text-signal">{domain}</span>
            <span className={filledClass(0)}>{contact}</span>
            <span className={filledClass(1)}>{fundraise}</span>
            <span className={filledClass(2)}>{headline}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
