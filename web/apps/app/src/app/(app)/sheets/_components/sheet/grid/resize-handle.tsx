"use client";

import type { GridHeader } from "./use-grid-table";

/** What marks a press as the handle's: the header cell's drag
 * listener runs in the capture phase, before this element could
 * stop anything, so it has to recognise the handle by this mark and
 * stand aside. */
export const RESIZE_HANDLE_ATTRIBUTE = "data-resize-handle";

/** The strip at a column's right edge that resizes it. Fifteen
 * pixels wide and straddling the border (five of them over the next
 * column), so the target is wider than the hairline it sits on, with
 * a four-pixel guide inside that lights up and runs down the grid on
 * hover, so the edge being moved is visible against every row.
 *
 * The resize is the table model's: the handler it hands out tracks
 * the pointer to release. The handle sits INSIDE the header cell
 * that also drags and opens the menu; the cell's drag listener
 * recognises the handle's presses by their mark and skips them, and
 * the press stops here so nothing beneath reads it as a click. A
 * double click puts the column back at the default. Pointer only,
 * like the gesture it mirrors; the keyboard has no resize today. */
export function ResizeHandle({ header }: { header: GridHeader }) {
  const resize = header.getResizeHandler();
  return (
    <div
      aria-hidden
      {...{ [RESIZE_HANDLE_ATTRIBUTE]: "" }}
      className="group/resize absolute top-0 z-10 h-full w-[15px] cursor-ew-resize"
      style={{ right: -5 }}
      onMouseDown={(event) => {
        event.stopPropagation();
        resize(event);
      }}
      onTouchStart={(event) => {
        event.stopPropagation();
        resize(event);
      }}
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => {
        event.stopPropagation();
        header.column.resetSize();
      }}
    >
      <div className="m-auto h-full w-1 transition-all duration-100 ease-in-out group-hover/resize:h-screen group-hover/resize:bg-signal" />
    </div>
  );
}
