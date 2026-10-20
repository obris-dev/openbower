import type { RenderableProbe } from "@bower/api";

import { deliveryWord, receiverFacts, TONE_CLASS } from "../../../_components/webhook-delivery";
import { PROBE_SENT_FOLD } from "./copy";

/** The add form's answer, above its button: the outcome word in its
 * tone and the receiver's facts on one line (every send here is a test
 * ping, so no label), then the error and the receiver's answer when
 * there are any, and the body as sent, folded: the one place the
 * envelope a receiver has to parse is shown. No box: one answer to one
 * question, not a log. */
export function ProbeOutcome({ probe }: { probe: RenderableProbe }) {
  const { attempt, envelope } = probe;
  const word = deliveryWord(attempt);
  return (
    <div className="space-y-1 text-xs" role="status">
      <p>
        <span className={`text-sm font-medium ${TONE_CLASS[word.tone]}`}>{word.line}</span>
        <span className="ml-3 text-muted">{receiverFacts(attempt).join(" | ")}</span>
      </p>
      {attempt.error && <p className="text-danger [overflow-wrap:anywhere]">{attempt.error}</p>}
      {attempt.response_excerpt && (
        <details className="text-muted">
          <summary className="cursor-pointer">Receiver&apos;s answer</summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-wash p-2 font-mono [overflow-wrap:anywhere]">
            {attempt.response_excerpt}
          </pre>
        </details>
      )}
      <details className="text-muted">
        <summary className="cursor-pointer">{PROBE_SENT_FOLD}</summary>
        <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-wash p-2 font-mono text-foreground">
          {JSON.stringify(envelope, null, 2)}
        </pre>
      </details>
    </div>
  );
}
