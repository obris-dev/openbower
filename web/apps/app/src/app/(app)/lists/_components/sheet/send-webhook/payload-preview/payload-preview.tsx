"use client";

import { TouchTarget } from "@bower/ui";
import type { RenderableDelivery } from "@bower/api";

import { DeliveryRow, deliveryLabel } from "../../../../../_components/webhook-delivery";
import { EDIT_PAYLOAD, EXAMPLE_PAYLOAD_TITLE, NOT_SENT_LINE, PREVIEW_HINT, PREVIEW_LEGEND, SENT_HINT, SENT_PAYLOAD_TITLE } from "../copy";
import type { PreviewLine } from "../lib/preview";
import { PayloadLines } from "./payload-lines";
import { RowStepper } from "./row-stepper";
import type { CellActions, SampleStepper } from "./types";

/** The drawer's centerpiece, shaped like one row of the delivery log
 * in both of its states. Before a send: "Not yet sent" over the
 * payload a test would carry, the server's values marked and the
 * cells edited where they sit (value, exclude, include); the payload
 * is the thesis, so it does not fold. After one: the delivery's
 * outcome and time over the payload that was sent, folded the way a
 * log row folds its details, with one way back to editing. The sample
 * row is chosen on the header, since a better example beats
 * hand-editing. */
export function PayloadPreview({
  id,
  lines,
  result,
  sample,
  cells,
  onEditAgain,
}: {
  id: string;
  lines: PreviewLine[];
  result: RenderableDelivery | null;
  sample: SampleStepper;
  cells: CellActions;
  onEditAgain: () => void;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-legend`} className="rounded-lg border border-hairline p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={`${id}-legend`} className="text-sm font-medium text-foreground">
            {PREVIEW_LEGEND}
          </h3>
          <p className="mt-1 text-xs text-muted">{result ? SENT_HINT : PREVIEW_HINT}</p>
          {result && (
            <button type="button" onClick={onEditAgain} className="relative mt-1 text-xs text-signal hover:underline">
              <TouchTarget>{EDIT_PAYLOAD}</TouchTarget>
            </button>
          )}
        </div>
        <RowStepper sample={sample} />
      </div>
      <div className="mt-3">
        {result ? (
          // A status, so the outcome of a send is announced where it
          // lands; the editable lines below it are not.
          <div role="status">
            <DeliveryRow delivery={result}>
            <details className="text-xs text-muted">
              <summary className="cursor-pointer">{SENT_PAYLOAD_TITLE}</summary>
              <PayloadLines lines={lines} cells={cells} />
            </details>
            </DeliveryRow>
          </div>
        ) : (
          <div className="space-y-1">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-sm font-medium text-muted">{NOT_SENT_LINE}</span>
              <span className="text-xs text-muted">{deliveryLabel({ type: "digest", test: true })}</span>
            </div>
            <p className="text-xs text-muted">{EXAMPLE_PAYLOAD_TITLE}</p>
            <PayloadLines lines={lines} cells={cells} />
          </div>
        )}
      </div>
    </section>
  );
}
