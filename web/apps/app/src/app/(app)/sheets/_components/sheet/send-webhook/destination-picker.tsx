"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button, FieldError, Label, Select } from "@bower/ui";
import { fetchWebhooks, webRoutes, type RenderableDestination } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { hostOf } from "../../../../_components/webhook-delivery";
import {
  DESTINATION_PLACEHOLDER,
  DESTINATIONS_FAILED_LINE,
  DESTINATIONS_LOADING_LINE,
  NO_DESTINATIONS_LINE,
} from "./copy";

const SELECT_ID = "send-webhook-destination";
const ERROR_ID = "send-webhook-destination-error";

/** The account's destinations in a select, read on mount and again on
 * Refresh (a destination added in Settings, which opens in a new tab
 * so this drawer keeps its state). Paused destinations are offered:
 * `enabled` gates the automatic lane, and a test is an explicit
 * choice, as the settings Test button is. An empty roster teaches the
 * next step; a failed read says so with a Retry and keeps the last
 * roster it had, so a pick never goes invisible. */
export function DestinationPicker({
  id,
  value,
  onChange,
  attempt,
  onRefresh,
  warned,
  gap,
  refusal,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  /** Bumped by the parent to re-read (the Refresh button, a refusal
   * saying the destination is gone). */
  attempt: number;
  onRefresh: () => void;
  warned: boolean;
  gap: string | null;
  refusal: string | null;
}) {
  const [destinations, setDestinations] = useState<RenderableDestination[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    async function load() {
      setLoading(true);
      setFailed(false);
      const res = await fetchWebhooks();
      if (!live) return;
      setLoading(false);
      if (redirectIfUnauthenticated(res)) return;
      if (res.status !== "ok") {
        setFailed(true);
        return;
      }
      setDestinations(res.data.items);
      // A pick the reloaded roster no longer holds is no pick: the
      // parent must not believe a destination is chosen.
      if (value !== "" && !res.data.items.some((destination) => destination.id === value)) onChange("");
    }
    void load();
    return () => {
      live = false;
    };
    // The roster is re-read on `attempt` only; `value`/`onChange` are
    // read at completion and must not restart the fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  const roster = destinations ?? [];
  const choosable = roster.length > 0;
  // One message slot: a refusal (the server said no) outranks a gap
  // (the user has not chosen yet), and a gap only makes sense while
  // there is something to choose from.
  const message = refusal ?? (choosable ? gap : null);
  const describedBy = message ? ERROR_ID : undefined;
  return (
    <div id={id} className="space-y-2">
      <div className="flex items-end justify-between gap-2">
        <Label htmlFor={SELECT_ID}>Destination</Label>
        <Button type="button" variant="ghost" size="sm" onClick={onRefresh} loading={loading}>
          <RefreshCw aria-hidden className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>
      {destinations === null && loading && <p className="text-xs text-faint">{DESTINATIONS_LOADING_LINE}</p>}
      {failed && (
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-danger">{DESTINATIONS_FAILED_LINE}</p>
          <Button type="button" size="sm" variant="secondary" onClick={onRefresh}>
            Retry
          </Button>
        </div>
      )}
      {destinations !== null && !choosable && (
        <p className="text-sm text-muted">
          {NO_DESTINATIONS_LINE}{" "}
          <Link href={webRoutes.settingsWebhooks} target="_blank" rel="noreferrer" className="underline">
            Open Settings
          </Link>
        </p>
      )}
      {choosable && (
        <Select
          id={SELECT_ID}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          invalid={refusal !== null}
          warned={warned}
          aria-describedby={describedBy}
        >
          <option value="">{DESTINATION_PLACEHOLDER}</option>
          {roster.map((destination) => (
            <option key={destination.id} value={destination.id}>
              {destination.label} | {hostOf(destination.url)}
              {destination.enabled ? "" : " | paused"}
            </option>
          ))}
        </Select>
      )}
      {message && (
        <FieldError id={ERROR_ID} tone={refusal ? "danger" : "warning"}>
          {message}
        </FieldError>
      )}
    </div>
  );
}
