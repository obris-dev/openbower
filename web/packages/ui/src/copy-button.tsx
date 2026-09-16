"use client";

import { Copy } from "lucide-react";
import { Button } from "./button";
import { useToast } from "./toast";

/** Copies a value to the clipboard on click (a keyboard path too, it is
 * a button). The clipboard is touched only in the handler, never at
 * render; the outcome is a toast, since nothing on the page changes. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const toast = useToast();

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Copied.");
    } catch {
      toast.error("Copy failed. Select the text and copy it yourself.");
    }
  }

  return (
    <Button type="button" variant="secondary" size="sm" onClick={() => void copy()}>
      <Copy aria-hidden className="mr-1.5 h-4 w-4" />
      {label}
    </Button>
  );
}
