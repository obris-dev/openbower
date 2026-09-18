// The drawer's draft of a webhook column, and the hops between it and
// the wire: what a fresh draft starts as, what a request carries (in
// sheet order), and whether an edit changed anything. Pure.

import type { ListColumn, WebhookColumnConfig, WebhookColumnConfigWire } from "@bower/api";

import { columnKind, exportableColumns } from "../../lib/column-kind.ts";

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
      waitKeys: new Set(columns.filter((column) => columnKind(column) === "ai").map((column) => column.key)),
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

/** Whether a save would change anything: key sets compared as sets. */
export function isDirty(draft: WebhookDraft, saved: WebhookColumnConfigWire): boolean {
  return (
    draft.destinationId !== saved.destination_id ||
    draft.intervalSeconds !== saved.interval_seconds ||
    draft.enabled !== saved.enabled ||
    !sameSet(draft.waitKeys, saved.wait_keys) ||
    !sameSet(draft.payloadKeys, saved.payload_keys)
  );
}

function sameSet(a: ReadonlySet<string>, b: readonly string[]): boolean {
  return a.size === b.length && b.every((key) => a.has(key));
}
