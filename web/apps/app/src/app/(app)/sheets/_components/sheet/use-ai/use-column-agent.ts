"use client";

import { useEffect, useState } from "react";
import { getColumnAgent, type ColumnAgentWire } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

export type ColumnAgentLoad =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; agent: ColumnAgentWire };

/** The agent filling a column, read when the edit drawer opens on it.
 * The drawer stays mounted through the load (one panel, one
 * transition) and seeds its draft when `ready` arrives; a failed read
 * renders as such with a Retry (bumps `attempt`). */
export function useColumnAgent(sheetId: string, columnKey: string): { load: ColumnAgentLoad; retry: () => void } {
  const [load, setLoad] = useState<ColumnAgentLoad>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    async function read() {
      const res = await getColumnAgent(sheetId, columnKey);
      if (!live) return;
      if (redirectIfUnauthenticated(res)) return;
      setLoad(res.status === "ok" ? { status: "ready", agent: res.data } : { status: "failed" });
    }
    void read();
    return () => {
      live = false;
    };
  }, [sheetId, columnKey, attempt]);
  return {
    load,
    retry: () => {
      setLoad({ status: "loading" });
      setAttempt((n) => n + 1);
    },
  };
}
