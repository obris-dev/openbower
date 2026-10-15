"use client";

import { Copy } from "lucide-react";
import { Button } from "./button";
import { useToast } from "./toast";

/** Copies a value to the clipboard on click (a keyboard path too, it is
 * a button). The clipboard is touched only in the handler, never at
 * render; the outcome is a toast, since nothing on the page changes.
 * `iconOnly` renders the glyph alone (the label becomes its accessible
 * name) for a spot beside the text it copies, where a labelled button
 * would push the text out of its box. */
export function CopyButton({ value, label = "Copy", iconOnly = false }: { value: string; label?: string; iconOnly?: boolean }) {
  const toast = useToast();

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Copied.");
    } catch {
      toast.error("Copy failed. Select the text and copy it yourself.");
    }
  }

  if (iconOnly) {
    return (
      <button
        type="button"
        aria-label={label}
        title={label}
        onClick={() => void copy()}
        className="shrink-0 rounded p-1 text-faint hover:bg-wash hover:text-foreground"
      >
        <Copy aria-hidden className="h-4 w-4" />
      </button>
    );
  }

  return (
    <Button type="button" variant="secondary" size="sm" onClick={() => void copy()}>
      <Copy aria-hidden className="mr-1.5 h-4 w-4" />
      {label}
    </Button>
  );
}
