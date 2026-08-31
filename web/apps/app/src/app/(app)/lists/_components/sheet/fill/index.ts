// The fill family: the sheet's attachment to its fill jobs (the poll
// loop; cell states ride the rows themselves), the surfaces that
// render a fill's
// progress (the tray, the tracker cell, the per-cell diagnosis), and
// the pure modules they derive from (eta, pace, staleness, scope, tray
// mode). Everything the family needs from itself it imports by file;
// this index is what the REST of the sheet may reach for.
export { AiCellState, DegradedToolMark, isDegradedFill, needsSearchDoor, type SearchDoor } from "./cell-state";
export { DEFAULT_SCOPE_ROWS, defaultScopeKind, effectiveRows, parseScopeRows, type ScopeChoice } from "./lib/fill-scope";
export { FillTrackerCell } from "./fill-tracker-cell";
export { FillsTray } from "./fills-tray";
export { useFill } from "./use-fill";
