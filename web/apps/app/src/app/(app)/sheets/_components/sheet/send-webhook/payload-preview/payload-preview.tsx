"use client";

import { Button, ErrorMessage, Skeleton, TouchTarget } from "@bower/ui";
import type { RenderableDelivery } from "@bower/api";

import { DeliveryRow, deliveryLabel } from "../../../../../_components/webhook-delivery";
import {
  EDIT_PAYLOAD,
  EXAMPLE_PAYLOAD_TITLE,
  NOT_SENT_LINE,
  PREVIEW_HINT,
  PREVIEW_LEGEND,
  RETRY,
  SENT_HINT,
  SENT_PAYLOAD_TITLE,
} from "../copy";
import type { PreviewLine } from "../lib/preview";
import { PayloadLines } from "./payload-lines";
import { RowStepper } from "./row-stepper";
import type { CellActions, SampleStepper } from "./types";

export type PreviewBox = {
  /** The lines to show, or null while nothing has arrived yet. */
  lines: PreviewLine[] | null;
  loading: boolean;
  failure: string | null;
  onRetry: () => void;
  /** Why nothing can be previewed yet (a missing choice), or null. */
  waitingLine: string | null;
};

/** The drawer's centerpiece, shaped like one row of the delivery log
 * in both of its states. Before a send: "Not yet sent" over the
 * payload the server says a test would carry, with the cells edited
 * where they sit (value, exclude, include); the payload is the thesis,
 * so it does not fold. After one: the delivery's outcome and time over
 * the payload that was sent, folded the way a log row folds its
 * details, with one way back to editing. The sample row is chosen on
 * the header, since a better example beats hand-editing. */
export function PayloadPreview({
  id,
  preview,
  result,
  sample,
  cells,
  onEditAgain,
}: {
  id: string;
  preview: PreviewBox;
  result: { delivery: RenderableDelivery; lines: PreviewLine[] } | null;
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
            <DeliveryRow delivery={result.delivery}>
              <details className="text-xs text-muted">
                <summary className="cursor-pointer">{SENT_PAYLOAD_TITLE}</summary>
                <PayloadLines lines={result.lines} cells={cells} />
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
            <Unsent preview={preview} cells={cells} />
          </div>
        )}
      </div>
    </section>
  );
}

function Unsent({ preview, cells }: { preview: PreviewBox; cells: CellActions }) {
  if (preview.waitingLine) return <p className="text-sm text-muted">{preview.waitingLine}</p>;
  if (preview.lines === null && preview.loading) return <PayloadSkeleton />;
  if (preview.lines === null && preview.failure) {
    return (
      <div className="space-y-2">
        <ErrorMessage message={preview.failure} />
        <Button type="button" size="sm" variant="secondary" loading={preview.loading} onClick={preview.onRetry}>
          {RETRY}
        </Button>
      </div>
    );
  }
  if (preview.lines === null) return <PayloadSkeleton />;
  return (
    <div aria-busy={preview.loading || undefined}>
      <PayloadLines lines={preview.lines} cells={cells} />
      {preview.failure && (
        <div className="mt-1 flex items-center gap-2">
          <p className="text-xs text-danger">{preview.failure}</p>
          <Button type="button" size="sm" variant="secondary" loading={preview.loading} onClick={preview.onRetry}>
            {RETRY}
          </Button>
        </div>
      )}
    </div>
  );
}

/** The shape the lines take, while the first envelope is on its way. */
function PayloadSkeleton() {
  return (
    <div className="mt-1 space-y-2 rounded-md bg-wash p-2" aria-hidden>
      {["w-1/4", "w-1/2", "w-1/3", "w-3/5", "w-1/5"].map((width) => (
        <Skeleton key={width} className={`h-3 motion-reduce:animate-none ${width}`} />
      ))}
    </div>
  );
}
