// The sheet's grid, a component family: the orchestrator composes the
// header band, the tracker row and the virtual body over one headless
// table model. The sheet reaches only for what is named here: the
// grid itself, the tracker's input shape, the header menu button's id
// (where an edit gesture hands focus back), the selection controls'
// shape, the write's "how" for its toast, and the two cell predicates
// a focus or a bar needs (which cells take a value, how to focus one).
export { SheetGrid } from "./sheet-grid";
export type { SheetFills } from "./tracker-row";
export { menuButtonId } from "./header-cell";
export type { RowSelectionControls } from "./row-selection-controls";
export type { WriteHow } from "./cell-actions";
export { focusCell } from "./focus-cell";
export { isEditable } from "./lib/cell-navigation";
