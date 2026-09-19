"use client";

import { useEffect, useState } from "react";
import { getColumnWebhook, type WebhookColumnConfigWire } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

export type WebhookConfigLoad =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; config: WebhookColumnConfigWire | null };

/** An existing webhook column's config, read when the drawer opens on
 * it; a null key is the add drawer, ready at once with no config. The
 * drawer stays mounted through the load (one panel, one transition)
 * and seeds its draft when `ready` arrives; a failed read renders as
 * such with a Retry (bumps `attempt`). */
export function useWebhookConfig(
  listId: string,
  columnKey: string | null,
): { load: WebhookConfigLoad; retry: () => void } {
  const [load, setLoad] = useState<WebhookConfigLoad>(
    columnKey === null ? { status: "ready", config: null } : { status: "loading" },
  );
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (columnKey === null) return;
    let live = true;
    async function read() {
      const res = await getColumnWebhook(listId, columnKey as string);
      if (!live) return;
      if (redirectIfUnauthenticated(res)) return;
      setLoad(res.status === "ok" ? { status: "ready", config: res.data } : { status: "failed" });
    }
    void read();
    return () => {
      live = false;
    };
  }, [listId, columnKey, attempt]);
  return {
    load,
    retry: () => {
      setLoad({ status: "loading" });
      setAttempt((n) => n + 1);
    },
  };
}
