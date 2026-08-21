// The ONE home for how a typed cell value becomes a link: the sheet
// and the agent test bench both render through this, so a scheme-less
// URL or an email behaves identically in both (two inline copies
// disagreed once, under a comment claiming they agreed).

// An href renders only when the declared TYPE is url/email AND the
// value's SHAPE is one (web/AGENTS.md): a url-typed cell holding
// "not found" must render as prose, never as https://not found.
const URL_SHAPE = /^(?:https?:\/\/\S+|[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:\/\S*)?)$/i;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** The href for a cell of the given declared type, or null when the
 * value renders as plain text. URL cells link even scheme-less
 * (https:// prepended); email cells link as mailto. */
export function cellHref(type: string, value: string): string | null {
  if (type === "url" && URL_SHAPE.test(value)) {
    return /^https?:\/\//i.test(value) ? value : `https://${value}`;
  }
  if (type === "email" && EMAIL_SHAPE.test(value)) return `mailto:${value}`;
  return null;
}

/** Whether the link leaves the app (drives target="_blank"). */
export function cellLinkIsExternal(type: string): boolean {
  return type === "url";
}
