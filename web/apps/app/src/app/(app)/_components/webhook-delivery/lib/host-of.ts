/** The host a destination's card shows; a URL the parser refuses shows
 * as itself rather than nothing. */
export function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
