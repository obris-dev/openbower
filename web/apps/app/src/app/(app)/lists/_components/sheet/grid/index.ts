// The sheet's grid, a component family: the orchestrator composes the
// header band, the tracker row and the virtual body over one headless
// table model. The sheet reaches only for the grid itself, the
// tracker's input shape, and the header menu button's id (where an
// edit gesture hands focus back).
export { SheetGrid } from "./sheet-grid";
export type { SheetFills } from "./tracker-row";
export { menuButtonId } from "./header-cell";
