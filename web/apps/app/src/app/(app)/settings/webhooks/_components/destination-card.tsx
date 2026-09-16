"use client";

import Link from "next/link";
import { webRoutes, type RenderableDestination } from "@bower/api";

import { deliveryRead, TONE_CLASS } from "../../../_components/webhook-delivery";
import { hostOf } from "../../../_components/webhook-delivery";

/** One destination on the roster: name, host, its header names, and
 * how its newest delivery went. The whole card links to the detail. */
export function DestinationCard({ destination }: { destination: RenderableDestination }) {
  const read = deliveryRead(destination);
  return (
    <li>
      <Link
        href={webRoutes.settingsWebhook(destination.id)}
        className="block space-y-1 rounded-lg border border-hairline bg-surface p-4 hover:bg-wash"
      >
        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{destination.label}</p>
          {!destination.enabled && (
            <span className="shrink-0 rounded-full bg-wash px-2 py-0.5 text-xs text-warning">Paused</span>
          )}
        </div>
        <p className="truncate text-xs text-muted">{hostOf(destination.url)}</p>
        {destination.header_names.length > 0 && (
          <p className="truncate text-xs text-faint">Headers: {destination.header_names.join(" | ")}</p>
        )}
        <p className={`text-xs ${TONE_CLASS[read.tone]}`}>{read.line}</p>
        {destination.last_delivery?.error && (
          <p className="text-xs text-danger [overflow-wrap:anywhere]">{destination.last_delivery.error}</p>
        )}
      </Link>
    </li>
  );
}
