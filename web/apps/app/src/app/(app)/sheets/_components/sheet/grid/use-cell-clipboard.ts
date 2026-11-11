"use client";

import { useCallback, useMemo, useRef } from "react";
import { useToast } from "@bower/ui";

// The CopyButton's two sentences, so a copy reads the same wherever
// it happens.
const COPIED = "Copied.";
const COPY_FAILED = "Copy failed. Select the text and copy it yourself.";
const NOTHING_TO_PASTE = "Nothing to paste.";
const PASTE_BLOCKED = "Paste blocked. Allow clipboard access in the browser and try again.";

/** The clipboard as the grid uses it. `copy` writes the value to the
 * system clipboard and remembers it; `read` asks the system clipboard
 * (a value copied outside the app pastes too). A browser with no read
 * API gets the remembered value, the last copy made here. A browser
 * that HAS the API and refuses the read (a denied or dismissed
 * permission) gets no fallback: the remembered value may be older
 * than what the person copied since, and a paste overwrites, so it is
 * blocked and said so. The clipboard is touched only in these
 * handlers, never at render. */
export function useCellClipboard(): {
  copy: (value: string) => Promise<void>;
  read: () => Promise<string | null>;
} {
  const toast = useToast();
  const remembered = useRef<string | null>(null);

  const copy = useCallback(
    async (value: string) => {
      remembered.current = value;
      try {
        await navigator.clipboard.writeText(value);
        toast.success(COPIED);
      } catch {
        toast.error(COPY_FAILED);
      }
    },
    [toast],
  );

  const read = useCallback(async (): Promise<string | null> => {
    let value: string | null = null;
    if (typeof navigator.clipboard?.readText !== "function") {
      value = remembered.current;
    } else {
      try {
        value = await navigator.clipboard.readText();
      } catch {
        toast.error(PASTE_BLOCKED);
        return null;
      }
    }
    if (value === null || value === "") {
      toast.error(NOTHING_TO_PASTE);
      return null;
    }
    return value;
  }, [toast]);

  // One object while the two handlers hold, so a memo over it holds.
  return useMemo(() => ({ copy, read }), [copy, read]);
}
