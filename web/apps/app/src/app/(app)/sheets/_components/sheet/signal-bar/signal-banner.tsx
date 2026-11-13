"use client";

import { Button, Card, cn } from "@bower/ui";
import type { LogDetail } from "@bower/api";

import { EDIT_SIGNAL_ACTION } from "../../../../_components/signals";
import { LinkButton } from "../../../../../_components/link-button";
import { BANNER_ACTION, signalBanner } from "./lib/signal-banner";
import { RECONNECT_ACTION } from "./copy";

/** What stops a signal's new rounds, when something does, with what a
 * person can do about it: Reconnect (asked in the confirm dialog) or Edit
 * signal (its settings). */
export function SignalBanner({
  settingsHref,
  log,
  onReconnect,
}: {
  /** The signal's settings, as the sheet's verbs name them. */
  settingsHref: string | null;
  log: LogDetail | null;
  /** Ask to reconnect (the confirm dialog's). */
  onReconnect: () => void;
}) {
  const banner = signalBanner(log);
  if (banner === null) return null;

  return (
    <div className="shrink-0 border-b border-hairline px-4 py-2">
      <Card
        role={banner.tone === "danger" ? "alert" : "status"}
        className={cn(
          "flex flex-wrap items-center justify-between gap-2 p-3 text-sm",
          banner.tone === "danger" ? "bg-danger-wash text-danger-strong" : "bg-warning-wash text-foreground",
        )}
      >
        <p className="min-w-0 [overflow-wrap:anywhere]">{banner.text}</p>
        {banner.action === BANNER_ACTION.RECONNECT && (
          <Button size="sm" onClick={onReconnect}>
            {RECONNECT_ACTION}
          </Button>
        )}
        {banner.action === BANNER_ACTION.EDIT && settingsHref !== null && (
          <LinkButton size="sm" href={settingsHref}>
            {EDIT_SIGNAL_ACTION}
          </LinkButton>
        )}
      </Card>
    </div>
  );
}
