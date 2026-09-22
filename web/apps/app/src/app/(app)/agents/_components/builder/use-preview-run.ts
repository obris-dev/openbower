"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@bower/ui";
import {
  fetchRun,
  isRunOpen,
  postPreviewRun,
  NODE_RUN_STALE_SECONDS,
  postRunCancel,
  UNKNOWN_RUN_STATUS,
  type AgentConfig,
  type CellRunResult,
  type RenderableRun,
} from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";

// Poll cadence (binary). A preview run is WORKER-SUPERVISED like any
// run: the loop is open-ended (each poll its own short bounded GET;
// the run lane guarantees termination through the attempt cap and
// the give-up path), blips back off (the error bound below exits
// before the doubling passes 8_192ms, so no ceiling constant exists
// to clamp against), and staleness is a WARNING fact judged against
// the run's latest state change, never a failure.
const TEST_POLL_INTERVAL_MS = 1_024;
// Consecutive polls the loop cannot read before it stops calling them
// blips (mirrors use-fill): a failed request, or a status this bundle
// has never heard of (an unheard-of TERMINAL member would otherwise
// read as open forever). Past this the run is unreadable from here (a
// pruned id answers 404 forever, a dead server answers nothing), and a
// loop that never exits holds the Test button busy for good. The run,
// if live, terminates server-side either way.
const MAX_POLL_ERRORS = 4;

/** The preview run's whole lifecycle: POST the run, poll it to terminal,
 * diagnose. A refused start (a row past its bounds, a config that
 * cannot run) toasts the server's own detail and stops; your own
 * earlier unclaimed run is superseded server-side, never refused. A
 * new run (or unmount) supersedes any in-flight loop via the
 * generation counter. Resolves the result, or null after its own
 * toast. `testStale` surfaces the quiet-run warning while a run is
 * still open. */
export function usePreviewRun(): {
  testBusy: boolean;
  testStale: boolean;
  runTest: (config: AgentConfig, row: Record<string, string>) => Promise<CellRunResult | null>;
} {
  const toast = useToast();
  const [testBusy, setTestBusy] = useState(false);
  const [testStale, setTestStale] = useState(false);
  const generationRef = useRef(0);
  // The run this hook is currently supervising, so unmount can stop
  // it server-side: a run nobody is watching would otherwise sit in
  // the queue and, once claimed, spend on an answer nobody reads. The
  // cancel reaches an unclaimed run only; one a worker already owns
  // runs to its end and keeps its result.
  const liveRunIdRef = useRef<string | null>(null);
  // Held so unmount can CLEAR it: the generation check already stops
  // the loop writing anything, but a backed-off wait (seconds, at the
  // error bound) keeps this whole closure alive after the builder is
  // gone until it fires (use-fill's rule).
  const sleepRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      generationRef.current += 1;
      if (sleepRef.current !== null) {
        clearTimeout(sleepRef.current);
        sleepRef.current = null;
      }
      if (liveRunIdRef.current !== null) {
        // Fire-and-forget: the page is going away, and a failed
        // cancel only means the run finishes on its own.
        void postRunCancel(liveRunIdRef.current);
        liveRunIdRef.current = null;
      }
    };
  }, []);

  function staleness(run: RenderableRun): boolean {
    // The run's one deadness window off the wire (its heartbeat freezes
    // for the whole of its execution, so the row-lease window would
    // flag a legitimately running row): quiet past it reads as a
    // warning (the run may still be queued behind work), never as
    // failure; a terminal status always ends the loop first.
    return Date.now() - Date.parse(run.heartbeat_at) > NODE_RUN_STALE_SECONDS * 1_000;
  }

  function lostSight(): null {
    setTestBusy(false);
    setTestStale(false);
    // Tier 3 copy: only this page can see that it cannot read the run.
    toast.error("This page can no longer follow the test run; run it again.", "Lost sight of the test run");
    return null;
  }

  async function pollToTerminal(initial: RenderableRun, generation: number): Promise<CellRunResult | null> {
    let run = initial;
    let pollErrors = 0;
    while (isRunOpen(run)) {
      if (generationRef.current !== generation) return null;
      setTestStale(staleness(run));
      const wait = TEST_POLL_INTERVAL_MS * 2 ** pollErrors;
      // The handle is LOCAL and the ref cleared only while it still
      // points at it: a superseding run's loop can store its handle
      // while this one is still sleeping (use-fill's rule).
      let handle: ReturnType<typeof setTimeout> | null = null;
      await new Promise<void>((resolve) => {
        handle = setTimeout(resolve, wait);
        sleepRef.current = handle;
      });
      if (sleepRef.current === handle) sleepRef.current = null;
      if (generationRef.current !== generation) return null;
      const polled = await fetchRun(run.id);
      if (generationRef.current !== generation) return null;
      if (polled.status === "unauthenticated") {
        // A dead session is terminal, not a blip: ensureOk routes to
        // login.
        setTestBusy(false);
        setTestStale(false);
        ensureOk(polled, toast, { title: "Test failed" });
        return null;
      }
      if (polled.status !== "ok") {
        // A blip must not abort a paid, still-computing run: back off
        // and keep supervising (the run terminates server-side
        // whatever this page can reach). BOUNDED: enough consecutive
        // failures is no longer a blip, and an unexitable loop wedges
        // the Test button behind a run this page can never read again.
        pollErrors += 1;
        if (pollErrors >= MAX_POLL_ERRORS) return lostSight();
        continue;
      }
      if (polled.data.status === UNKNOWN_RUN_STATUS) {
        // A status this bundle cannot name counts like a failed read:
        // it may be an added open member (keep supervising, backed
        // off) or an added terminal one (the run is finished and this
        // page can never tell), and only the bound tells them apart.
        pollErrors += 1;
        if (pollErrors >= MAX_POLL_ERRORS) return lostSight();
        run = polled.data;
        continue;
      }
      pollErrors = 0;
      run = polled.data;
    }
    setTestBusy(false);
    setTestStale(false);
    // Terminal: nothing left for the unmount cancel to stop. The
    // lost-sight and login exits above deliberately KEEP the ref (the
    // run may still be live, and unmount should still try to stop it).
    if (liveRunIdRef.current === run.id) liveRunIdRef.current = null;
    if (run.result) {
      // The one terminal with a result: a claimed run always lands (a
      // cancel reaches only an unclaimed one), so a paid diagnosis is
      // never stranded behind a "stopped" toast.
      return run.result;
    }
    if (run.status === "abandoned") {
      // Stopped before a worker claimed it: superseded by a newer test
      // of yours (another tab). A refusal story, not a failure one.
      toast.error("The test was stopped before it finished; run it again.");
      return null;
    }
    // Done with no result: the drafted config could not run at all (a
    // model or tool that went away between the start and the run).
    toast.error("The test finished without a result; check the model and tools, then run it again.");
    return null;
  }

  async function runTest(config: AgentConfig, row: Record<string, string>): Promise<CellRunResult | null> {
    const generation = ++generationRef.current;
    setTestBusy(true);
    setTestStale(false);
    // The POST answers immediately with a run to poll: the agentic
    // loop takes seconds to minutes, and holding the request open for
    // it ties up a connection for nothing.
    const started = await postPreviewRun(config, row);
    if (generationRef.current !== generation) {
      // Superseded (or unmounted) DURING the create: the run exists
      // server-side but was never stored in the ref, so nothing else
      // will ever stop it. The local id, never the ref, so a newer
      // run's own id is not touched.
      if (started.status === "ok") void postRunCancel(started.data.id);
      return null;
    }
    if (!ensureOk(started, toast, { title: "Test failed" })) {
      setTestBusy(false);
      return null;
    }
    // Held for the unmount cancel. A superseding click just
    // overwrites it: the server abandons the old run on start.
    liveRunIdRef.current = started.data.id;
    return pollToTerminal(started.data, generation);
  }

  return { testBusy, testStale, runTest };
}
