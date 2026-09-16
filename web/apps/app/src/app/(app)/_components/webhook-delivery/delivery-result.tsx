import type { RenderableDelivery } from "@bower/api";

import { deliveryLabel, deliveryWord, TONE_CLASS } from "./lib/delivery-read";
import { formatTime } from "./lib/format-time";

/** One delivery's outcome, inline: the status word in its tone, what
 * it carried, the receiver's status and timing, then the error and
 * the excerpt when there is one (text, never markup). Rendered under a
 * Test button wherever one lives. */
export function DeliveryResult({ delivery }: { delivery: RenderableDelivery }) {
  const word = deliveryWord(delivery);
  const label = deliveryLabel(delivery);
  const facts = [
    label || null,
    delivery.http_status === null ? null : `HTTP ${delivery.http_status}`,
    `${delivery.duration_ms.toLocaleString("en-US")} ms`,
  ].filter((fact): fact is string => fact !== null);
  return (
    <div className="space-y-1 rounded-lg border border-hairline p-3" role="status">
      <p className={`text-sm font-medium ${word.tone === "muted" ? "text-foreground" : TONE_CLASS[word.tone]}`}>
        {word.line}
        {` | ${facts.join(" | ")}`}
      </p>
      <p className="text-xs text-faint">{formatTime(delivery.created_at)}</p>
      {delivery.error && <p className="text-xs text-danger [overflow-wrap:anywhere]">{delivery.error}</p>}
      {delivery.response_excerpt && (
        <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-wash p-2 font-mono text-xs text-muted [overflow-wrap:anywhere]">
          {delivery.response_excerpt}
        </pre>
      )}
    </div>
  );
}
