"use client";

import { useRef, type RefObject } from "react";
import { useVirtualizer, type VirtualItem } from "@tanstack/react-virtual";

import { ROW_HEIGHT } from "./lib/column-sizing";

// Rows painted past each edge of the viewport, so a flick does not
// reach a blank band before the next render lands. Binary.
const OVERSCAN_ROWS = 12;

/** Which rows are on screen, and where each one sits, over the
 * sheet's own scroll region. Every row is ROW_HEIGHT tall by
 * construction (the row component paints the same constant), so the
 * estimate is exact and nothing is measured. The body does not start
 * at the top of the scroll region (the header band and the tracker
 * row come first), so the body's own offset is the virtualizer's
 * margin and a row positions by its start minus that margin. The
 * ONE place the grid knows its virtualizer library. */
export function useVirtualRows(
  count: number,
  scrollRef: RefObject<HTMLDivElement | null>,
): {
  bodyRef: RefObject<HTMLDivElement | null>;
  items: VirtualItem[];
  totalSize: number;
  scrollMargin: number;
} {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    scrollMargin: bodyRef.current?.offsetTop ?? 0,
  });
  return {
    bodyRef,
    items: virtualizer.getVirtualItems(),
    totalSize: virtualizer.getTotalSize(),
    scrollMargin: virtualizer.options.scrollMargin,
  };
}
