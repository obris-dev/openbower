// The fill family: the sheet's attachment to its fill runs (the poll
// loop; cell states ride the rows themselves), the surfaces that
// render fill progress (the per-column tracker cell, the footer's
// passive glance, the per-cell diagnosis), and the pure modules they
// derive from (eta, pace, staleness, scope, tracker, glance).
// Everything the family needs from itself it imports by file; this
// index is what the REST of the sheet may reach for.
export {
  AiCellState,
  CauseMark,
  DegradedToolMark,
  isDegradedFill,
  needsSearchProvider,
  type SearchProviderChoice,
} from "./cell-state";
export { DEFAULT_SCOPE_ROWS, defaultScopeKind, effectiveRows, parseScopeRows, type ScopeChoice } from "./lib/fill-scope";
export { FillsGlance } from "./fills-glance";
export { isLiveStatus, type LiveRun } from "./lib/live-status";
export { FillTrackerCell } from "./fill-tracker-cell";
export { useFill } from "./use-fill";
