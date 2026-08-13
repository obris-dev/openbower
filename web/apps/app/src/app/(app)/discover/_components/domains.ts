// Seed-input parsing shared by the controls and the exclude filter.

// Mirrors the backend's inline-domains bound: sized for a whole-CRM
// paste. Parsing reports the overflow instead of silently dropping it.
export const MAX_SEEDS = 5000;

export type ParsedDomains = { domains: string[]; dropped: number };

/** The backend's canonical bare-domain form, mirrored: results carry
 * normalized domains, so anything compared against them (the exclude
 * filter) must normalize too or `Acme.com`, `www.acme.com`, and pasted
 * URLs silently match nothing. */
function normalizeDomain(raw: string): string {
  let s = raw.trim().toLowerCase();
  s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//, ""); // scheme
  s = s.split(/[/?#]/, 1)[0] ?? ""; // path, query, fragment
  s = s.split("@").pop() ?? ""; // credentials
  s = s.split(":", 1)[0] ?? ""; // port
  s = s.replace(/^www\./, "");
  return s.replace(/\.$/, "");
}

/** Split on commas/whitespace, normalize, drop blanks, dedupe, cap;
 * `dropped` says how many deduped entries fell past the cap so the UI
 * can SAY so (a silent slice would quietly search a different cohort
 * than pasted). */
export function parseDomains(raw: string): ParsedDomains {
  const seen = raw.split(/[\s,]+/).map(normalizeDomain).filter(Boolean);
  const unique = [...new Set(seen)];
  return { domains: unique.slice(0, MAX_SEEDS), dropped: Math.max(0, unique.length - MAX_SEEDS) };
}
