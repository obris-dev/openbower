"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button, FieldError, Label, Select } from "@bower/ui";
import { fetchWebhooks, webRoutes, type RenderableDestination } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { hostOf } from "../../../../_components/webhook-delivery";
import { NO_DESTINATIONS_LINE } from "./copy";

const SELECT_ID = "send-webhook-destination";
const ERROR_ID = "send-webhook-destination-error";

/** The account's enabled destinations in a select, read on mount and
 * again on Refresh (a destination added in Settings, which opens in a
 * new tab so this drawer keeps its state). An empty roster teaches
 * the next step; a failed read says so with a Retry. */
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

  useEffect(() => {
    let live = true;
    async function load() {
      const res = await fetchWebhooks();
      if (!live) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status !== "ok") {
        setFailed(true);
        return;
      }
      setFailed(false);
      setDestinations(res.data.items.filter((destination) => destination.enabled));
    }
    void load();
    return () => {
      live = false;
    };
  }, [attempt]);

  const describedBy = gap || refusal ? ERROR_ID : undefined;
  return (
    <div id={id} className="space-y-2">
      <div className="flex items-end justify-between gap-2">
        <Label htmlFor={SELECT_ID}>Destination</Label>
        <Button type="button" variant="ghost" size="sm" onClick={onRefresh}>
          <RefreshCw aria-hidden className="mr-1.5 h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>
      {destinations === null && !failed && <p className="text-xs text-faint">Loading destinations…</p>}
      {failed && (
        <div className="space-y-2">
          <p className="text-xs text-danger">Destinations could not be loaded.</p>
          <Button type="button" size="sm" variant="secondary" onClick={onRefresh}>
            Retry
          </Button>
        </div>
      )}
      {destinations !== null && !failed && destinations.length === 0 && (
        <p className="text-sm text-muted">
          {NO_DESTINATIONS_LINE}{" "}
          <Link href={webRoutes.settingsWebhooks} target="_blank" rel="noreferrer" className="underline">
            Open Settings
          </Link>
        </p>
      )}
      {destinations !== null && !failed && destinations.length > 0 && (
        <Select
          id={SELECT_ID}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          invalid={refusal !== null}
          aria-describedby={describedBy}
          className={warned ? "ring-1 ring-inset ring-warning-edge" : undefined}
        >
          <option value="">Choose a destination…</option>
          {destinations.map((destination) => (
            <option key={destination.id} value={destination.id}>
              {destination.label} | {hostOf(destination.url)}
            </option>
          ))}
        </Select>
      )}
      {gap && (
        <FieldError id={ERROR_ID} tone="warning">
          {gap}
        </FieldError>
      )}
      {refusal && <FieldError id={ERROR_ID}>{refusal}</FieldError>}
    </div>
  );
}
