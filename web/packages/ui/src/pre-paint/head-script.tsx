import { storedWidthsScript, type StoredWidthsOptions } from "./stored-widths";

/** A blocking inline script, rendered first in <body> beside the theme
 * and sidebar scripts (LAYOUT.md rule 7). Only trusted app constants
 * are interpolated in, as JS literals that cannot close the element;
 * the entity id comes from the URL in the browser, never from a prop. */
export function StoredWidthsHeadScript(options: StoredWidthsOptions) {
  return <script dangerouslySetInnerHTML={{ __html: storedWidthsScript(options) }} />;
}
