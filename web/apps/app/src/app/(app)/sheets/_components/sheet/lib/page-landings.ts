// The loaded rows change two ways: a re-read replaces them whole, and a
// next page adds to their end. Each lands only if neither landed while it
// was out: a re-read over a page that just landed would drop it, and a
// page behind a re-read would continue from a cursor the re-read moved.

export type PageLandings = {
  /** A read going out: the token it lands with. */
  start: () => number;
  /** Whether a read that went out with `token` may land; landing moves the
   * count, so any read still out behind it is dropped. */
  land: (token: number) => boolean;
};

export function createPageLandings(): PageLandings {
  let landed = 0;
  return {
    start() {
      return landed;
    },
    land(token) {
      if (token !== landed) return false;
      landed += 1;
      return true;
    },
  };
}
