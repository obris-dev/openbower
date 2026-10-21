/** Focus a cell by its marks, the one imperative focus the grid does
 * (a row gone, a key moving between cells, a row just made): the
 * cells are not in any React tree the caller holds, only in the DOM
 * under the body. Scrolls the cell into view without jumping. */
export function focusCell(root: ParentNode | null, rowId: string, columnKey: string | null): boolean {
  const column = columnKey === null ? "" : `[data-column-key="${columnKey}"]`;
  const cell = root?.querySelector<HTMLElement>(`[data-row-id="${rowId}"][tabindex]${column}`);
  if (!cell) return false;
  cell.focus({ preventScroll: true });
  cell.scrollIntoView({ block: "nearest", inline: "nearest" });
  return true;
}
