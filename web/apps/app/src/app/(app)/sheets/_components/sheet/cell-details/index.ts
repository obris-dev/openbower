// The cell details family: the docked panel where one cell is read in full
// (its value, and for an agent's cell how sure its run was and why),
// the confidence mark, and the pure modules they derive from. This
// index is what the rest of the sheet may reach for.
export { CellDetailsPanel, type DetailsTarget } from "./cell-details-panel";
export { SELECT_HOW, useCellDetails, type CellDetails, type CellRef, type SelectHow } from "./use-cell-details";
export { hasDetails, opensDetails } from "./lib/details-view";
export { UNCONFIRMED_LABEL, VIEW_DETAILS } from "./lib/confidence";
