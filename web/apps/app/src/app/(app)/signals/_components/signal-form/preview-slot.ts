// The account's one open preview, as this page holds it. The signals
// service refuses a create while one is open, so every release this page
// owes (the run it holds, a create that landed after its Test was
// superseded) is queued, and a create waits for the queue first.
// A cancel the service refuses (or never answers) leaves that preview
// open; the next create is then refused in the server's own words, which
// the Test shows, so a failed release is never silent.

export type PreviewSlot = {
  /** The open preview this page now holds. */
  hold: (id: string) => void;
  /** Stop holding a preview that finished on its own. */
  drop: (id: string) => void;
  /** Queue the cancel of the preview this page holds, if any. */
  release: () => void;
  /** A create in flight: if it lands once its Test was superseded, queue
   * its cancel, since no one will ever hold it. */
  track: (createdId: Promise<string | null>, superseded: () => boolean) => void;
  /** Resolves once every queued release has been answered. */
  free: () => Promise<void>;
};

export function createPreviewSlot(cancel: (id: string) => Promise<unknown>): PreviewSlot {
  let held: string | null = null;
  let queue: Promise<void> = Promise.resolve();

  function enqueue(step: () => Promise<void>) {
    const prior = queue;
    queue = (async () => {
      await prior;
      await step();
    })();
  }

  return {
    hold(id) {
      held = id;
    },
    drop(id) {
      if (held === id) held = null;
    },
    release() {
      const id = held;
      held = null;
      if (id === null) return;
      enqueue(async () => {
        await cancel(id);
      });
    },
    track(createdId, superseded) {
      enqueue(async () => {
        const id = await createdId;
        if (id !== null && superseded()) await cancel(id);
      });
    },
    async free() {
      await queue;
    },
  };
}
