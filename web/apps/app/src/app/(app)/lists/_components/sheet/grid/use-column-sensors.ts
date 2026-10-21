"use client";

import { MouseSensor, useSensor, useSensors } from "@dnd-kit/core";

// Distance, not delay: a press on the header must stay ambiguous
// until it has travelled far enough to be a drag rather than a
// click on the menu button that IS the cell. 8px is past the noise
// of a click that moved slightly.
const DRAG_ACTIVATION_DISTANCE = 8;

/** The one sensor a column drag listens to: the MOUSE.
 *
 * No touch sensor: the sheet is a horizontally scrolling grid, so a
 * horizontal drag on its sticky header competes with the primary
 * navigation gesture on a phone, and the usual fix (a long press
 * before the drag arms) makes both worse.
 *
 * No keyboard sensor: dnd-kit's lifts from the element holding these
 * listeners, which is the header CELL, so arming it would mean a
 * second focusable per column beside the menu button. The menu
 * reaches the same move from the keyboard (its panel is a Popover, so
 * Tab walks the items rather than the arrow keys a Menu would give),
 * and it is the path touch uses too, so only the mouse gets the
 * shortcut. */
export function useColumnSensors() {
  return useSensors(useSensor(MouseSensor, { activationConstraint: { distance: DRAG_ACTIVATION_DISTANCE } }));
}
