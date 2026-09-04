"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getFills,
  loginUrl,
  postFillCancel,
  type ColumnFillSummary,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { livenessRead, type LiveRun } from "./lib/live-status";

// Poll cadence (binary). Open-ended like every worker-supervised
// poll (the bench's use-test-fill rides the same doctrine): the LOOP
// has no budget, never that a request stays open. Each poll is its
// own short bounded GET, and liveness rides the worker's heartbeat on
// the envelope, not this browser.
const FILL_POLL_INTERVAL_MS = 4_096;
// Consecutive poll blips tolerated before the trouble fact surfaces
// (binary). Blips never stop a loop supervising a live run: the fill
// continues server-side regardless, so the only honest client move is
// a warning that updates are not reaching this page.
const MAX_POLL_ERRORS = 4;
// The backoff ceiling for a failing poll (binary): a downed API must
// not be hammered by every open sheet, and a minute is still a live
// supervisor from the user's point of view.
const MAX_POLL_BACKOFF_MS = 65_536;

/** The sheet's attachment to its fill runs: re-attach on load (the
 * browser is never a fill's liveness signal), an open-ended poll loop
 * with a generation counter while any run is live, and Stop per run.
 * Cell states are NOT read here: they ride the rows the sheet already
 * holds. The page ships LIVE runs only; a terminal run's story (its
 * status, its error) is the summaries' to tell, so `runs` is exactly
 * the in-flight work, and polling ends when BOTH halves of the
 * payload read quiet (see tick's liveness note). */
export function useFill(
  listId: string,
): {
  runs: LiveRun[];
  summaries: ColumnFillSummary[];
  pollTrouble: boolean;
  refresh: () => Promise<void>;
  stop: (runId: string) => Promise<string | null>;
} {
  const [runs, setRuns] = useState<LiveRun[]>([]);
  const [summaries, setSummaries] = useState<ColumnFillSummary[]>([]);
  const [pollTrouble, setPollTrouble] = useState(false);
  const generationRef = useRef(0);
  const runsRef = useRef<LiveRun[]>([]);
  const sleepRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const tick = useCallback(
    async (generation: number): Promise<"ok-live" | "ok-idle" | "blip" | "unauthenticated" | "superseded"> => {
      const fills = await getFills(listId);
      if (generationRef.current !== generation) return "superseded";
      if (fills.status === "unauthenticated") return "unauthenticated";
      if (fills.status !== "ok") return "blip";
      // The trusted runs and the loop's liveness are ONE pure
      // decision (lib/live-status.livenessRead), pinned in the node
      // lane where a hook cannot be.
      const read = livenessRead(fills.data.runs, fills.data.columns);
      runsRef.current = read.runs;
      setRuns(read.runs);
      setSummaries(fills.data.columns);
      // The poll is the FILLS read alone. Cell states ride the rows
      // the sheet already holds; walking them again here would be a
      // second full page-through of the loaded sheet every tick,
      // fetching each row's data only to throw it away and keep
      // .states. Nothing needs clearing when the last fill goes
      // terminal either: pending is derived from queued tasks on LIVE
      // fills, and stopping a fill abandons its tasks before it flips,
      // so both legs of that derivation go false on their own.
      return read.live ? "ok-live" : "ok-idle";
    },
    [listId],
  );

  const refresh = useCallback(async () => {
    // Every call supersedes any in-flight loop and starts its own
    // (re-attach on load, restart after a submit, extend after Load
    // more); the loop exits once nothing is live.
    const generation = ++generationRef.current;
    let errors = 0;
    for (;;) {
      const outcome = await tick(generation);
      if (generationRef.current !== generation || outcome === "superseded") return;
      if (outcome === "unauthenticated") {
        window.location.href = loginUrl();
        return;
      }
      if (outcome === "blip") {
        errors += 1;
        // The loop NEVER stops on blips: a fill runs server-side
        // whatever this page can reach, so quitting would strand a
        // live run behind copy that says updates are still coming.
        // Repeated failures back off instead, up to a ceiling.
        if (errors >= MAX_POLL_ERRORS) setPollTrouble(true);
      } else {
        errors = 0;
        setPollTrouble(false);
        if (outcome === "ok-idle") return;
      }
      const backoff = Math.min(FILL_POLL_INTERVAL_MS * 2 ** Math.max(0, errors - 1), MAX_POLL_BACKOFF_MS);
      // Held so unmount can CLEAR it. The generation check below already
      // stops the loop writing anything, but a backed-off wait is up to
      // MAX_POLL_BACKOFF_MS, and until it fires the timer keeps this
      // whole closure alive after the sheet is gone.
      // The handle is LOCAL and the ref is cleared only while it
      // still points at it: refresh() is called concurrently (stop,
      // and again after a submit), so a second loop can store its
      // handle while this one is still sleeping, and clearing
      // unconditionally would drop the live one and leave unmount
      // nothing to cancel.
      let handle: ReturnType<typeof setTimeout> | null = null;
      await new Promise<void>((resolve) => {
        handle = setTimeout(resolve, backoff);
        sleepRef.current = handle;
      });
      if (sleepRef.current === handle) sleepRef.current = null;
      if (generationRef.current !== generation) return;
    }
  }, [tick]);

  useEffect(() => {
    void refresh();
    return () => {
      // Unmount (or a dep change) supersedes the in-flight loop AND
      // drops its pending sleep, which would otherwise hold the
      // closure for the rest of the backoff.
      generationRef.current += 1;
      if (sleepRef.current !== null) {
        clearTimeout(sleepRef.current);
        sleepRef.current = null;
      }
    };
  }, [refresh]);

  const stop = useCallback(
    async (runId: string): Promise<string | null> => {
      const res = await postFillCancel(listId, runId);
      if (redirectIfUnauthenticated(res)) return null;
      if (res.status !== "ok") return res.message;
      // The cancel echo is terminal and `runs` is the LIVE set, so the
      // run leaves it now rather than a tick later; the summary read
      // behind refresh() carries its terminal story.
      runsRef.current = runsRef.current.filter((run) => run.id !== res.data.id);
      setRuns(runsRef.current);
      void refresh();
      return null;
    },
    [listId, refresh],
  );

  return { runs, summaries, pollTrouble, refresh, stop };
}
