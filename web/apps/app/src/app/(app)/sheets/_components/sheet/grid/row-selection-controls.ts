/** The grid's view of the sheet's row selection: the sheet owns the
 * state (`use-row-selection`), the grid's gutter cells and header box
 * read and call. */
export type RowSelectionControls = {
  isSelected: (rowId: string) => boolean;
  /** Any row picked: every gutter then shows its box, not its number. */
  selecting: boolean;
  headerState: "none" | "some" | "all";
  /** Nothing to pick (no rows): the select-all box is disabled. */
  disabled: boolean;
  onToggle: (rowId: string, range: boolean) => void;
  onToggleAll: () => void;
};

