import type { RenderableDelivery } from "@bower/api";

import { DeliveryRow } from "./delivery-row";

/** One delivery's outcome, inline under a Test button: the log's own
 * row, in a box, announced as a status. One reading of a delivery
 * everywhere it shows. */
export function DeliveryResult({ delivery }: { delivery: RenderableDelivery }) {
  return (
    <div className="rounded-lg border border-hairline p-3" role="status">
      <DeliveryRow delivery={delivery} />
    </div>
  );
}
