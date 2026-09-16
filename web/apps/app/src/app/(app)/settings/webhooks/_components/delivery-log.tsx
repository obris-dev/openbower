"use client";

import { Button } from "@bower/ui";
import { UNKNOWN_DELIVERY_KIND, WEBHOOK_ID_HEADER, type RenderableDelivery } from "@bower/api";

import { deliveryWord, TONE_CLASS } from "./lib/delivery-read";
import { formatTime } from "./lib/format-time";

// A kind this bundle cannot name gets no chip: naming it would claim
// what it carried.
const KIND_LABEL: Record<Exclude<RenderableDelivery["kind"], typeof UNKNOWN_DELIVERY_KIND>, string> = {
  test: "Test",
  digest: "Digest",
};

/** One delivery per row, newest first: the outcome, what it carried,
 * the receiver's status and timing, then the error and the excerpt
 * when there is one (text, never markup). A failed read renders as
 * such at either end: before any page, and under the rows when a
 * later page did not land. */
export function DeliveryLog({
  items,
  failed,
  hasMore,
  loadingMore,
  onRetry,
  onLoadMore,
}: {
  items: RenderableDelivery[] | null;
  failed: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onRetry: () => void;
  onLoadMore: () => void;
}) {
  if (items === null && !failed) return <p className="text-xs text-faint">Loading deliveries…</p>;
  if (items === null) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-danger">Deliveries could not be loaded.</p>
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (items.length === 0) {
    return <p className="text-sm text-muted">No deliveries yet. Send a test to see one here.</p>;
  }
  // A bounded, scrolling region: the log grows inside it, never the
  // page, and Load more sits at its foot where a reader reaches it.
  return (
    <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-hairline px-3">
      <ul className="divide-y divide-hairline">
        {items.map((delivery) => (
          <DeliveryRow key={delivery.id} delivery={delivery} />
        ))}
      </ul>
      {failed && (
        <div className="flex items-center justify-between gap-3 py-2">
          <p className="text-xs text-danger">More deliveries could not be loaded.</p>
          <Button size="sm" variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
      {hasMore && !failed && (
        <div className="sticky bottom-0 bg-surface py-2">
          <Button variant="secondary" size="sm" fullWidth loading={loadingMore} onClick={onLoadMore}>
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}

function DeliveryRow({ delivery }: { delivery: RenderableDelivery }) {
  const word = deliveryWord(delivery);
  const facts = [
    delivery.kind === UNKNOWN_DELIVERY_KIND ? null : KIND_LABEL[delivery.kind],
    delivery.http_status === null ? null : `HTTP ${delivery.http_status}`,
    `${delivery.duration_ms.toLocaleString("en-US")} ms`,
  ].filter((fact): fact is string => fact !== null);
  return (
    <li className="space-y-1 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`text-sm font-medium ${TONE_CLASS[word.tone]}`}>{word.line}</span>
        <span className="text-xs text-muted">{facts.join(" | ")}</span>
        <span className="ml-auto text-xs text-faint">{formatTime(delivery.created_at)}</span>
      </div>
      {delivery.error && <p className="text-xs text-danger [overflow-wrap:anywhere]">{delivery.error}</p>}
      {delivery.response_excerpt && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Receiver&apos;s answer</summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-md bg-wash p-2 font-mono text-xs [overflow-wrap:anywhere]">
            {delivery.response_excerpt}
          </pre>
        </details>
      )}
      <p className="font-mono text-[11px] text-faint">
        {WEBHOOK_ID_HEADER} {delivery.id}
      </p>
    </li>
  );
}
