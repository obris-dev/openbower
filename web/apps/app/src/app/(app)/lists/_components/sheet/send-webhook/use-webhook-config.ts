"use client";

import { useEffect, useState } from "react";
import { getColumnWebhook, type WebhookColumnConfigWire } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

export type WebhookConfigLoad =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; config: WebhookColumnConfigWire };

/** An existing webhook column's config, read when the drawer opens on
 * it. The editor mounts only on `ready`, so its state initialises from
 * a value that has arrived; a failed read renders as such with a
 * Retry (bumps `attempt`). */
export function useWebhookConfig(listId: string, columnKey: string): { load: WebhookConfigLoad; retry: () => void } {
  const [load, setLoad] = useState<WebhookConfigLoad>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    async function read() {
      const res = await getColumnWebhook(listId, columnKey);
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
