"use client";

import { useState } from "react";
import { useToast } from "@bower/ui";
import { fetchLookalikesResult, runCursor, type LookalikeItem } from "@bower/api";

import { lookalikeCsv, saveCsv } from "../csv";

// One page of the client-side build; mirrors the backend's page cap.
const BUILD_PAGE = 1000;

/** The client-side CSV build as a hook: page the run to the cutoff,
 * filter excludes during assembly (exactly like the on-screen rows),
 * save as a Blob. `building` is the rows fetched so far (null = idle),
 * for progress copy on the trigger. The paged-build shape is the
 * template any future sheet/list export follows. */
export function useCsvDownload({
  runId,
  cutoff,
  excluded,
}: {
  runId: string | null;
  cutoff: number;
  excluded: Set<string>;
}) {
  const toast = useToast();
  const [building, setBuilding] = useState<number | null>(null);

  async function download() {
    if (!runId) return;
    setBuilding(0);
    const rows: LookalikeItem[] = [];
    let fetchedThrough = 0;
    // The FIRST cursor is the contract's from-the-top form; every later
    // one is the server's next_cursor, followed verbatim.
    let cursor: string | null = runCursor(runId);
    try {
      while (cursor !== null && fetchedThrough < cutoff) {
        const res = await fetchLookalikesResult({
          cursor,
          limit: Math.min(BUILD_PAGE, cutoff - fetchedThrough),
        });
        if (res.status !== "ok") {
          toast.error("Download failed part-way. Try again.");
          return;
        }
        const items = res.data.items;
        if (items.length === 0) break;
        const lastRank = items[items.length - 1]?.rank ?? 0;
        if (lastRank <= fetchedThrough) break; // a non-advancing page must never loop forever
        fetchedThrough = lastRank;
        for (const item of items) {
          if (!excluded.has(item.company.domain)) rows.push(item);
        }
        setBuilding(rows.length);
        cursor = res.data.next_cursor;
      }
      saveCsv(`lookalikes-${runId}.csv`, lookalikeCsv(rows));
    } finally {
      setBuilding(null);
    }
  }

  return { building, download };
}
