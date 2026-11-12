"use client";

import { useSyncExternalStore } from "react";

import { formatMoment, type TimeFormat } from "./local-time-format";

const neverChanges = () => () => {};

/** A moment in the reader's own time zone. The server cannot know that
 * zone, so the server render (and the hydration that must match it)
 * reads in UTC, named so, and the browser renders the local reading
 * straight after: a hand-written local format would disagree between
 * the two, which is a hydration error. */
export function LocalTime({ iso, format, className }: { iso: string; format: TimeFormat; className?: string }) {
  const hydrated = useSyncExternalStore(
    neverChanges,
    () => true,
    () => false,
  );
  const text = formatMoment(iso, format, hydrated ? undefined : "UTC");
  return (
    <time dateTime={iso} className={className}>
      {text}
    </time>
  );
}
