"use client";

import { useEffect, useRef, useState } from "react";
import { fetchWebhookDeliveries, type ApiResult, type RenderableDeliveriesPage, type RenderableDelivery } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

/** A destination's delivery log: the first page on mount, more by
 * keyset, and a prepend for a delivery this page just made. `items`
 * null before the first read lands (a failed read must not look like
 * an empty log); `failed` after any read that did not land, first page
 * or a later one, and `retry` repeats whichever it was. */
export function useDeliveries(destinationId: string): {
  items: RenderableDelivery[] | null;
  failed: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  retry: () => void;
  loadMore: () => void;
  prepend: (delivery: RenderableDelivery) => void;
} {
  const [items, setItems] = useState<RenderableDelivery[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  // A generation per read: a read that lands after unmount, or after a
  // newer read started, is superseded and touches nothing.
  const generation = useRef(0);

  // One landing for a page read, whoever started it.
  function land(res: ApiResult<RenderableDeliveriesPage>, append: boolean) {
    if (redirectIfUnauthenticated(res)) return;
    if (res.status !== "ok") {
      setFailed(true);
      return;
    }
    setFailed(false);
    setItems((prev) => (append && prev ? [...prev, ...res.data.items] : res.data.items));
    // An empty-string cursor must read as done.
    setCursor(res.data.next_cursor || null);
  }

  useEffect(() => {
    const mine = ++generation.current;
    async function first() {
      const res = await fetchWebhookDeliveries(destinationId);
      if (mine !== generation.current) return;
      land(res, false);
    }
    void first();
    return () => {
      generation.current += 1;
    };
  }, [destinationId]);

  async function firstPage() {
    const mine = ++generation.current;
    const res = await fetchWebhookDeliveries(destinationId);
    if (mine !== generation.current) return;
    land(res, false);
  }

  async function loadMore() {
    if (!cursor || loadingMore) return;
    const mine = ++generation.current;
    setLoadingMore(true);
    const res = await fetchWebhookDeliveries(destinationId, cursor);
    // The busy flag clears whoever wins the generation, so a superseded
    // read can never leave Load more stuck.
    setLoadingMore(false);
    if (mine !== generation.current) return;
    land(res, true);
  }

  function prepend(delivery: RenderableDelivery) {
    setItems((prev) => [delivery, ...(prev ?? [])]);
  }

  return {
    items,
    failed,
    hasMore: Boolean(cursor),
    loadingMore,
    // Retry repeats the read that failed: the first page before any
    // landed, the next page after.
    retry: () => void (items === null ? firstPage() : loadMore()),
    loadMore: () => void loadMore(),
    prepend,
  };
}
