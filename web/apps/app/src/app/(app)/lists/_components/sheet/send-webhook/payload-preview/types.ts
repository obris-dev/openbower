import type { ListColumn } from "@bower/api";

export type SampleStepper = {
  position: number;
  index: number;
  count: number;
  onStep: (delta: 1 | -1) => void;
};

/** What the payload's cells can do: edit a value, exclude a column,
 * include one of the columns not yet in it. The last cell cannot be
 * excluded, so an empty payload is unrepresentable rather than refused. */
export type CellActions = {
  labels: Record<string, string>;
  missing: ListColumn[];
  removable: boolean;
  onEdit: (key: string, value: string) => void;
  onRemove: (key: string) => void;
  onAdd: (key: string) => void;
};
