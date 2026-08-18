// Domain identity, one spelling: mirrors the kernel's normalize_domain
// exactly; agreement is pinned by the shared fixture
// (packages/kernel/fixtures/domain_normalization.json) run by both
// sides' tests.

// LDH hostname labels, matching the Python gate exactly.
const HOSTNAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
const MAX_DOMAIN_LENGTH = 253;

function isCanonicalIpv4(value: string): boolean {
  const parts = value.split(".");
  if (parts.length !== 4) return false;
  return parts.every((p) => /^\d+$/.test(p) && !(p.length > 1 && p.startsWith("0")) && Number(p) <= 255);
}

/** The canonical bare-domain form: results carry normalized domains,
 * so anything compared against them (the exclude filter, seeding) must
 * normalize the same way or `Acme.com`, `café.com`, and pasted URLs
 * silently match nothing. */
export function normalizeDomain(raw: string): string {
  let s = raw.trim().toLowerCase();
  s = s.replace(/^[a-z][a-z0-9+.-]*:\/\//, ""); // any scheme
  s = s.split(/[/?#]/, 1)[0] ?? ""; // path, query, fragment
  s = s.split("@").pop() ?? ""; // credentials
  s = s.split(":", 1)[0] ?? ""; // port
  s = s.replace(/^www\./, "").replace(/\.+$/, "");
  if (!s) return "";
  // Fullwidth digits fold to ASCII exactly as UTS-46 will map them, so
  // the IP-literal rule below judges the same string Python's
  // post-mapping check judges (１.１ must not reach the URL parser's
  // IPv4 rewriter looking like a hostname).
  s = s.replace(/[\uFF10-\uFF19]/g, (c) => String(c.charCodeAt(0) - 0xff10));
  if (!s) return "";
  // Percent-encoding is refused, not decoded: the URL parser would
  // decode %2e into a dot the Python side never sees.
  if (s.includes("%")) return "";
  // A numeric last label exists only on IP literals; decided BEFORE the
  // URL parser, which would REWRITE shorthand/hex/octal forms ("1.1",
  // "0x7f.0.0.1") into addresses the Python side never produces.
  if (/^\d+$/.test(s.split(".").pop() ?? "")) return isCanonicalIpv4(s) ? s : "";
  // IDNA via the URL parser: the platform's UTS-46, the same profile
  // the kernel's idna package applies.
  try {
    s = new URL(`http://${s}`).hostname;
  } catch {
    return "";
  }
  if (!s.includes(".") || s.length > MAX_DOMAIN_LENGTH || !HOSTNAME.test(s)) return "";
  // Re-checked AFTER the parser: UTS-46 maps fullwidth digits into
  // ASCII the pre-parse check never saw.
  if (/^\d+$/.test(s.split(".").pop() ?? "") && !isCanonicalIpv4(s)) return "";
  return s;
}
