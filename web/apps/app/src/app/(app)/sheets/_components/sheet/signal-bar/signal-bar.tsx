"use client";

import { useState, type RefObject } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { Label, LocalTime, Select, Switch, TIME_FORMAT, cn, useToast } from "@bower/ui";
import { LOG_STATUS, type LogDetail } from "@bower/api";

import { RECONNECT_ACTION, SignalStatus, isHeld } from "../../../../_components/signals";
import { PERIODS, type Period } from "../lib/periods";
import { LAST_READ, LIVE_LABEL, REFRESH_FAILED, NOT_YET_READ, PERIOD_NAMES, PERIOD_PICKER_LABEL, REFRESH_ACTION } from "./copy";

const PERIOD_ID = "signal-period";
const LIVE_ID = "signal-live";
/** A signal's sheet, as a log reads: where the signal stands and when it
 * was last read, the period on screen, a refresh, and Live. The period is
 * the URL's, so a switch draws that period's first page fresh. */
export function SignalBar({
  log,
  period,
  refresh,
  onReconnect,
  refreshRef,
  live,
  onLiveChange,
}: {
  log: LogDetail | null;
  period: Period;
  /** Re-read the loaded rows and the signal; answers whether both read. */
  refresh: () => Promise<boolean>;
  /** Ask to reconnect (the confirm dialog's): a Reconnect needed pill is
   * the way in, as the banner's button is. */
  onReconnect: () => void;
  /** The Refresh button, where focus lands once a reconnect's opener is
   * gone. */
  refreshRef: RefObject<HTMLButtonElement | null>;
  /** Live, owned by the sheet, whose one trouble line it reports to. */
  live: boolean;
  onLiveChange: (on: boolean) => void;
}) {
  const router = useRouter();
  const toast = useToast();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [refreshing, setRefreshing] = useState(false);
  const held = isHeld(log);

  function choosePeriod(next: string) {
    const query = new URLSearchParams(searchParams.toString());
    query.set("period", next);
    router.replace(`${pathname}?${query.toString()}`, { scroll: false });
  }


  async function refreshNow() {
    if (refreshing) return;
    setRefreshing(true);
    const read = await refresh();
    setRefreshing(false);
    // A person asked: a refresh that could not read says so (Live's own
    // failures say so in the bar instead).
    if (!read) toast.error(REFRESH_FAILED);
  }

  return (
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-hairline px-4 py-2 text-sm">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        {log && log.status === LOG_STATUS.RECONNECT ? (
          <button
            type="button"
            onClick={onReconnect}
            title={RECONNECT_ACTION}
            className="rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600 hover:opacity-80"
          >
            <SignalStatus status={log.status} held={held} />
          </button>
        ) : (
          log && <SignalStatus status={log.status} held={held} />
        )}
        {/* Only what the page knows: a signal it could not read says
            nothing here, and one never read yet says so plainly. */}
        {log && (
          <span className="text-muted">
            {log.last_polled_at ? (
              <>
                {LAST_READ} <LocalTime iso={log.last_polled_at} format={TIME_FORMAT.MINUTE} />
              </>
            ) : (
              NOT_YET_READ
            )}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Select
          id={PERIOD_ID}
          aria-label={PERIOD_PICKER_LABEL}
          value={period}
          onChange={(event) => choosePeriod(event.target.value)}
          className="w-40"
        >
          {PERIODS.map((each) => (
            <option key={each} value={each}>
              {PERIOD_NAMES[each]}
            </option>
          ))}
        </Select>
        <button
          ref={refreshRef}
          type="button"
          onClick={() => void refreshNow()}
          disabled={refreshing}
          aria-label={REFRESH_ACTION}
          title={REFRESH_ACTION}
          className="rounded-md p-2 text-faint hover:bg-wash hover:text-foreground disabled:cursor-wait"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", refreshing && "animate-spin motion-reduce:animate-none")} />
        </button>
        <div className="flex items-center gap-2">
          <Switch id={LIVE_ID} checked={live} onChange={onLiveChange} />
          <Label htmlFor={LIVE_ID} className="text-sm">
            {LIVE_LABEL}
          </Label>
        </div>
      </div>
    </div>
  );
}
