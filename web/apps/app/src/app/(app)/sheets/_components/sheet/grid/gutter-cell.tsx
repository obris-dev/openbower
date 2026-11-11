"use client";

import { useRef, type PointerEvent, type ReactNode } from "react";
import { Checkbox, cn } from "@bower/ui";

import { GUTTER_WIDTH } from "./lib/column-sizing";

/** The leading cell every band shares: sticky to the left edge so the
 * row number (or the header's mark) stays put while the columns
 * scroll under it. Its background is opaque for the same reason, or
 * the cells would show through as they pass. */
export function GutterCell({
  className,
  rowId,
  children,
}: {
  className?: string;
  /** The row this gutter cell leads, for a press that lands here. */
  rowId?: string;
  children?: ReactNode;
}) {
  return (
    <div
      data-row-id={rowId}
      className={cn(
        "sticky left-0 z-10 flex flex-none items-center justify-center border-r border-hairline bg-surface text-xs text-faint",
        className,
      )}
      style={{ width: GUTTER_WIDTH }}
    >
      {children}
    </div>
  );
}

/** A body row's gutter on a sheet that can be selected: the row
 * number, which gives way to the row's checkbox on hover or focus,
 * and stays the box while the row is picked or any row is. The box
 * covers the whole cell, so a tap on the number toggles on touch,
 * where there is no hover. Shift is read off the press, since the
 * primitive's change carries no key. */
export function RowGutterCell({
  rowId,
  number,
  selected,
  selecting,
  onToggle,
}: {
  rowId: string;
  number: number;
  selected: boolean;
  selecting: boolean;
  onToggle: (range: boolean) => void;
}) {
  const shiftRef = useRef(false);
  const shown = selected || selecting;
  return (
    // No `relative` here: it would replace the gutter's `sticky`, and
    // the box inside positions against the sticky cell just as well.
    <GutterCell className="group/gutter select-none border-b" rowId={rowId}>
      <span
        aria-hidden
        // Hidden for a VISIBLE focus only: a mouse click leaves focus on
        // the box without showing it, and the number must come back.
        className={cn("group-hover:invisible group-has-[:focus-visible]/gutter:invisible", shown && "invisible")}
      >
        {number}
      </span>
      <Checkbox
        checked={selected}
        aria-label={`Select row ${number}`}
        onPointerDown={(event: PointerEvent<HTMLElement>) => {
          shiftRef.current = event.shiftKey;
        }}
        onChange={() => onToggle(shiftRef.current)}
        className={cn(
          "absolute inset-0 m-auto opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
          shown && "opacity-100",
        )}
      />
    </GutterCell>
  );
}
