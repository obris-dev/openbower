import type { ReactNode } from "react";
import { WEBHOOK_ID_HEADER, type RenderableDelivery } from "@bower/api";

import { deliveryFacts, deliveryWord, TONE_CLASS } from "./lib/delivery-read";
import { formatTime } from "./lib/format-time";

/** One delivery, the way every surface shows one: the outcome word in
 * its tone, what it carried, the receiver's status and timing, the
 * time; then the error and the receiver's answer when there is one
 * (text, never markup), anything the caller folds under it, and the
 * id the receiver saw. Element-agnostic, so a log wraps it in a list
 * item and a panel in a box. */
export function DeliveryRow({ delivery, children }: { delivery: RenderableDelivery; children?: ReactNode }) {
  const word = deliveryWord(delivery);
  const facts = deliveryFacts(delivery);
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className={`text-sm font-medium ${TONE_CLASS[word.tone]}`}>{word.line}</span>
        <span className="text-xs text-muted">{facts.join(" | ")}</span>
        <span className="ml-auto text-xs text-faint">{formatTime(delivery.created_at)}</span>
      </div>
      {delivery.error && <p className="text-xs text-danger [overflow-wrap:anywhere]">{delivery.error}</p>}
      {delivery.response_excerpt && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Receiver&apos;s answer</summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-md bg-wash p-2 font-mono text-xs [overflow-wrap:anywhere]">
            {delivery.response_excerpt}
          </pre>
        </details>
      )}
      {children}
      <p className="font-mono text-[11px] text-faint">
        {WEBHOOK_ID_HEADER} {delivery.id}
      </p>
    </div>
  );
}
