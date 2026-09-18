"use client";

import { useEffect, useRef, useState } from "react";
import { postColumnWebhookPreview, type WebhookColumnTestBody, type WebhookEnvelopeJson } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

// Long enough to fold a burst of keystrokes into one request, short
// enough that the box reads as live.
const PREVIEW_DEBOUNCE_MS = 256;

export type PreviewState = {
  /** The newest envelope on record, kept while the next request is in flight. */
  envelope: WebhookEnvelopeJson | null;
  loading: boolean;
  failure: { message: string; code: string | null } | null;
  retry: () => void;
};

type Answer = { key: string; envelope: WebhookEnvelopeJson | null; failure: PreviewState["failure"] };

/** The envelope a send of `body` would carry, re-requested (debounced)
 * whenever the body changes: destination, wait keys, payload keys,
 * sample row, edited cells. A null body (nothing previewable yet)
 * shows nothing. The state held is the last ANSWER and the body it
 * answered; everything else derives, so the effect only schedules the
 * request and a superseded answer (an older generation) is dropped. */
export function usePreview(listId: string, body: WebhookColumnTestBody | null): PreviewState {
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);
  // The body as a key: the effect re-runs when what would be sent
  // changes, not when the object holding it is rebuilt.
  const bodyKey = body === null ? "" : JSON.stringify(body);

  useEffect(() => {
    const mine = ++generation.current;
    if (bodyKey === "") return;
    const timer = setTimeout(() => {
      void request();
    }, PREVIEW_DEBOUNCE_MS);
    async function request() {
      const res = await postColumnWebhookPreview(listId, JSON.parse(bodyKey) as WebhookColumnTestBody);
      if (mine !== generation.current) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status === "ok") {
        setAnswer({ key: bodyKey, envelope: res.data.envelope, failure: null });
        return;
      }
      setAnswer((prev) => ({
        key: bodyKey,
        // A failed refresh keeps the last envelope on screen beside its line.
        envelope: prev?.envelope ?? null,
        failure: { message: res.message, code: res.code ?? null },
      }));
    }
    return () => clearTimeout(timer);
  }, [listId, bodyKey, attempt]);

  const active = bodyKey !== "";
  const answered = active && answer !== null && answer.key === bodyKey;
  return {
    envelope: active ? (answer?.envelope ?? null) : null,
    loading: active && !answered,
    failure: answered ? answer.failure : null,
    retry: () => setAttempt((n) => n + 1),
  };
}
