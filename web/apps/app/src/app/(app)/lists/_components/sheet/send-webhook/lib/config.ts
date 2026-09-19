// The drawer's draft of a webhook column, and the hops between it and
// the wire: what a fresh draft starts as, what a request carries (in
// sheet order), and whether an edit changed anything. Pure.

import type { ListColumn, WebhookColumnConfig, WebhookColumnConfigWire } from "@bower/api";

import { exportableColumns } from "../../lib/exportable-columns.ts";

export type WebhookDraft = {
  destinationId: string;
  waitKeys: ReadonlySet<string>;
  payloadKeys: ReadonlySet<string>;
  intervalSeconds: number;
  enabled: boolean;
};

/** Add: no destination, every AI column waited on (the common case is
 * "when everything completes"; the user removes to send sooner), every
 * data column in the payload, the default cadence. Edit: the stored
 * config intersected with the columns the sheet still has (a key the
 * sheet lost is dropped, not a phantom switch). */
export function initialDraft(
  columns: readonly ListColumn[],
  config: WebhookColumnConfigWire | null,
  defaults: { intervalSeconds: number },
): WebhookDraft {
  const keys = new Set(columns.map((column) => column.key));
  if (config === null) {
    return {
      destinationId: "",
      waitKeys: new Set(columns.filter((column) => column.kind === "ai").map((column) => column.key)),
      payloadKeys: new Set(exportableColumns(columns).map((column) => column.key)),
      intervalSeconds: defaults.intervalSeconds,
      enabled: true,
    };
  }
  return {
    destinationId: config.destination_id,
    waitKeys: new Set(config.wait_keys.filter((key) => keys.has(key))),
    payloadKeys: new Set(config.payload_keys.filter((key) => keys.has(key))),
    intervalSeconds: config.interval_seconds,
    enabled: config.enabled,
  };
}

/** A wait-key change with the toggled columns' siblings carried
 * along: the server waits on an agent's PATH, so every column that
 * agent fills waits together, and a picker that let one be unchecked
 * alone would show a choice the next read undoes. */
export function withSiblings(
  columns: readonly ListColumn[],
  before: ReadonlySet<string>,
  after: ReadonlySet<string>,
): Set<string> {
  const nodeOf = new Map(columns.flatMap((column) => (column.kind === "ai" ? [[column.key, column.node_id]] : [])));
  const siblingsOf = (key: string) =>
    columns.filter((column) => column.kind === "ai" && column.node_id === nodeOf.get(key)).map((column) => column.key);
  const next = new Set(after);
  for (const key of after) if (!before.has(key)) for (const sibling of siblingsOf(key)) next.add(sibling);
  for (const key of before) if (!after.has(key)) for (const sibling of siblingsOf(key)) next.delete(sibling);
  return next;
}

/** The request's lists in SHEET order (the order the user sees; the
 * server stores what it is given). */
export function bodyFor(draft: WebhookDraft, columns: readonly ListColumn[]): WebhookColumnConfig {
  const keys = columns.map((column) => column.key);
  return {
    destination_id: draft.destinationId,
    wait_keys: keys.filter((key) => draft.waitKeys.has(key)),
    payload_keys: keys.filter((key) => draft.payloadKeys.has(key)),
    interval_seconds: draft.intervalSeconds,
  };
}

/** Whether a save would change anything the user did: the draft
 * against the draft the config SEEDED (both intersected with the
 * sheet the same way), so a stored key the sheet has lost since the
 * page loaded does not light Save on open. Key sets compared as sets. */
export function isDirty(draft: WebhookDraft, seeded: WebhookDraft): boolean {
  return (
    draft.destinationId !== seeded.destinationId ||
    draft.intervalSeconds !== seeded.intervalSeconds ||
    draft.enabled !== seeded.enabled ||
    !sameSet(draft.waitKeys, seeded.waitKeys) ||
    !sameSet(draft.payloadKeys, seeded.payloadKeys)
  );
}

function sameSet(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
  return a.size === b.size && [...b].every((key) => a.has(key));
}
