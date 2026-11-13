"use client";

import { useEffect, useRef, useState } from "react";
import {
  PREVIEW_SLOW_DOWN_KIND,
  PREVIEW_STATUS,
  UNKNOWN_PREVIEW_STATUS,
  cancelSignalPreview,
  createSignalPreview,
  fetchSignalEvents,
  fetchSignalPreview,
  isPreviewOpen,
  type ApiResult,
  type KnownSignal,
  type PreviewRun,
  type SignalSetup,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { FAILURE, LOST_SIGHT, NO_EVENTS } from "./copy";
import { createPreviewSlot } from "./preview-slot";
import { setupKey } from "./setup";

// A preview is WORKER-SUPERVISED: the signals service's previewer judges
// it and its own deadline ends it, so the loop is open-ended, each poll a
// short bounded GET.
const TEST_POLL_INTERVAL_MS = 1_024;
// Consecutive polls the loop cannot read (a failed request, or a status
// this bundle cannot name) before it lets go of the Test button; the
// preview ends server-side either way.
const MAX_POLL_ERRORS = 4;

/** What the results box says besides the rows: a line of copy, or the
 * judge asking for a pause until a time. */
export type TestNotice = { kind: "message"; text: string } | { kind: "slow_down"; text: string; retryAt: string | null };

/** A Test's whole lifecycle: the newest events, the preview, and its poll
 * to terminal. One preview may be open per account, so a new Test (and
 * leaving the page) cancels this page's own open one first. A new Test
 * or unmount supersedes any loop in flight through the generation
 * counter. */
export function useSignalTest(): {
  busy: boolean;
  run: PreviewRun | null;
  testedKey: string | null;
  notice: TestNotice | null;
  runTest: (signal: KnownSignal, setup: SignalSetup, events: number) => Promise<void>;
} {
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<PreviewRun | null>(null);
  const [testedKey, setTestedKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<TestNotice | null>(null);
  const generationRef = useRef(0);
  // The open preview this page started, released by a new Test and by
  // unmount: the account's one open slot is otherwise held, and a judge
  // keeps spending on events nobody reads.
  const [slot] = useState(() => createPreviewSlot(cancelSignalPreview));
  // Held so unmount can clear it: a backed-off wait keeps the closure
  // alive after the page is gone.
  const sleepRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      generationRef.current += 1;
      if (sleepRef.current !== null) {
        clearTimeout(sleepRef.current);
        sleepRef.current = null;
      }
      slot.release();
    };
  }, [slot]);

  function stop(next: TestNotice | null) {
    setBusy(false);
    setNotice(next);
  }

  function settle(terminal: PreviewRun) {
    slot.drop(terminal.id);
    setRun(terminal);
    if (terminal.status !== PREVIEW_STATUS.FAILED || terminal.error === null) {
      stop(null);
      return;
    }
    const text = FAILURE[terminal.error.kind];
    if (terminal.error.kind === PREVIEW_SLOW_DOWN_KIND) {
      stop({ kind: "slow_down", text, retryAt: terminal.retry_at });
      return;
    }
    stop({ kind: "message", text });
  }

  async function pollToTerminal(initial: PreviewRun, generation: number) {
    let current = initial;
    let pollErrors = 0;
    while (isPreviewOpen(current) || current.status === UNKNOWN_PREVIEW_STATUS) {
      const wait = TEST_POLL_INTERVAL_MS * 2 ** pollErrors;
      // The handle is LOCAL and the ref cleared only while it still points
      // at it: a superseding loop can store its own while this one sleeps.
      let handle: ReturnType<typeof setTimeout> | null = null;
      await new Promise<void>((resolve) => {
        handle = setTimeout(resolve, wait);
        sleepRef.current = handle;
      });
      if (sleepRef.current === handle) sleepRef.current = null;
      if (generationRef.current !== generation) return;
      const polled = await fetchSignalPreview(current.id);
      if (generationRef.current !== generation) return;
      if (redirectIfUnauthenticated(polled)) return;
      if (polled.status !== "ok" || polled.data.status === UNKNOWN_PREVIEW_STATUS) {
        pollErrors += 1;
        if (pollErrors >= MAX_POLL_ERRORS) {
          stop({ kind: "message", text: LOST_SIGHT });
          return;
        }
        continue;
      }
      pollErrors = 0;
      current = polled.data;
      setRun(current);
    }
    settle(current);
  }

  async function runTest(signal: KnownSignal, setup: SignalSetup, events: number) {
    const generation = ++generationRef.current;
    setBusy(true);
    setNotice(null);
    // The last results stay up while the next load, so a changed filter
    // reads as the list updating, not emptying.
    slot.release();
    const recent = await fetchSignalEvents(signal, events);
    if (generationRef.current !== generation) return;
    if (redirectIfUnauthenticated(recent)) return;
    if (recent.status === "error") {
      stop({ kind: "message", text: recent.message });
      return;
    }
    if (recent.data.items.length === 0) {
      stop({ kind: "message", text: NO_EVENTS });
      return;
    }
    const eventIds = recent.data.items.map((event) => event.id);
    await slot.free();
    if (generationRef.current !== generation) return;
    const creating = createSignalPreview(signal, setup, eventIds);
    slot.track(createdId(creating), () => generationRef.current !== generation);
    const started = await creating;
    if (generationRef.current !== generation) return;
    if (redirectIfUnauthenticated(started)) return;
    if (started.status === "error") {
      // The server writes this copy (an open preview elsewhere on the
      // account reads in its words), so it renders as sent.
      stop({ kind: "message", text: started.message });
      return;
    }
    setTestedKey(setupKey(setup));
    setRun(started.data);
    if (!isPreviewOpen(started.data) && started.data.status !== UNKNOWN_PREVIEW_STATUS) {
      settle(started.data);
      return;
    }
    slot.hold(started.data.id);
    await pollToTerminal(started.data, generation);
  }

  return { busy, run, testedKey, notice, runTest };
}

/** The id a create landed with, or null when it was refused. */
async function createdId(creating: Promise<ApiResult<PreviewRun>>): Promise<string | null> {
  const started = await creating;
  return started.status === "ok" ? started.data.id : null;
}
