"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@bower/ui";
import {
  fetchTestRun,
  TEST_RUN_ACTIVE_CODE,
  testAgent,
  type AgentConfig,
  type AgentTestResult,
  type AgentTestRun,
} from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";

// Poll cadence (binary). The BUDGET is the runtime's own worst case,
// carried on the run envelope (poll_budget_seconds): a run genuinely
// cannot outlive it, so past budget + margin the loop stops and the
// server's LARGER stale window later presents the orphan as failed
// for whoever polls next.
const TEST_POLL_INTERVAL_MS = 1_024;
const TEST_POLL_MARGIN_MS = 65_536;
// Transient poll failures tolerated before the run surfaces an error
// (the run keeps computing server-side regardless); mirrors the
// discover search's tolerance.
const MAX_POLL_ERRORS = 3;

/** The test run's whole lifecycle: POST, poll to terminal, diagnose.
 * A refused start (409: another run is live) toasts the server's own
 * detail and stops; deliberately no re-attach (another run's result
 * belongs to another config). A new run (or unmount) supersedes any
 * in-flight loop via the generation counter. Resolves the result, or
 * null after its own toast (failure, budget, contract breach). */
export function useTestRun(): {
  testBusy: boolean;
  runTest: (config: AgentConfig, row: Record<string, string>) => Promise<AgentTestResult | null>;
} {
  const toast = useToast();
  const [testBusy, setTestBusy] = useState(false);
  const generationRef = useRef(0);
  useEffect(() => {
    return () => {
      generationRef.current += 1;
    };
  }, []);

  async function pollToTerminal(initial: AgentTestRun, generation: number): Promise<AgentTestResult | null> {
    const deadline = Date.now() + initial.poll_budget_seconds * 1_000 + TEST_POLL_MARGIN_MS;
    let run = initial;
    let pollErrors = 0;
    while (run.status === "pending") {
      if (generationRef.current !== generation) return null;
      if (Date.now() > deadline) {
        setTestBusy(false);
        // The id survives in the message: the run may still complete
        // server-side, and whoever operates it can find it by id.
        toast.error(`The test never finished (run ${run.id}); run it again.`);
        return null;
      }
      await new Promise((resolve) => setTimeout(resolve, TEST_POLL_INTERVAL_MS));
      if (generationRef.current !== generation) return null;
      const polled = await fetchTestRun(run.id);
      if (generationRef.current !== generation) return null;
      if (polled.status === "unauthenticated") {
        // A dead session is terminal, not a blip (the discover
        // sibling's semantics): ensureOk routes to login.
        setTestBusy(false);
        ensureOk(polled, toast, { title: "Test failed" });
        return null;
      }
      if (polled.status !== "ok") {
        // A single blip must not abort a paid, still-computing run.
        pollErrors += 1;
        if (pollErrors >= MAX_POLL_ERRORS) {
          setTestBusy(false);
          ensureOk(polled, toast, { title: "Test failed" });
          return null;
        }
        continue;
      }
      pollErrors = 0;
      run = polled.data;
    }
    setTestBusy(false);
    if (run.status === "failed") {
      toast.error(
        // The server's error carries the profile-aware follow-up
        // (check the logs locally, contact support hosted); the
        // no-error fallback stays plain rather than hedging.
        run.error ? `The test run failed: ${run.error}` : "The test run failed without a reason; run it again.",
      );
      return null;
    }
    if (!run.result) {
      // Reachable two ways (a version mismatch, or a bad server-side
      // write presented honestly): neither is the agent's fault, and
      // the next step is the same.
      toast.error("The test finished without a result (something went wrong server-side); run it again.");
      return null;
    }
    return run.result;
  }

  async function runTest(config: AgentConfig, row: Record<string, string>): Promise<AgentTestResult | null> {
    const generation = ++generationRef.current;
    setTestBusy(true);
    // The POST answers immediately with a run to poll: the agentic
    // loop takes seconds to minutes, and holding the request open for
    // it ties up a connection for nothing.
    const started = await testAgent(config, row);
    if (generationRef.current !== generation) return null;
    if (started.status === "error" && started.code === TEST_RUN_ACTIVE_CODE) {
      // A refusal, not a failure: the server's own copy says whose
      // run is live and what to do.
      setTestBusy(false);
      toast.error(started.message, "Test not started");
      return null;
    }
    if (!ensureOk(started, toast, { title: "Test failed" })) {
      setTestBusy(false);
      return null;
    }
    return pollToTerminal(started.data, generation);
  }

  return { testBusy, runTest };
}
