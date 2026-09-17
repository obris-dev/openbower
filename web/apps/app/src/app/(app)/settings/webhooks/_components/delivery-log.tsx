"use client";

import { Button } from "@bower/ui";
import type { RenderableDelivery } from "@bower/api";

import { DeliveryRow } from "../../../_components/webhook-delivery";

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
          <li key={delivery.id} className="py-3">
            <DeliveryRow delivery={delivery} />
          </li>
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
