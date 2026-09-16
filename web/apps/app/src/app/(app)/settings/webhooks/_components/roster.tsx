"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button, EmptyState, useToast } from "@bower/ui";
import { fetchWebhooks, MAX_WEBHOOK_DESTINATIONS, webRoutes, type RenderableDestination } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { Breadcrumbs } from "../../../_components/breadcrumbs";
import { AddDestinationDrawer } from "./add-destination-drawer";
import { EMPTY_SUBTITLE, EMPTY_TITLE } from "./copy";
import { DestinationCard } from "./destination-card";

/** The roster as cards, the add drawer, and the cap. A created
 * destination is prepended from the drawer's response (no refetch);
 * the cap refusal re-reads the roster, since the server just said it
 * changed under this page. */
export function Roster({ initialDestinations }: { initialDestinations: RenderableDestination[] }) {
  const toast = useToast();
  const [destinations, setDestinations] = useState(initialDestinations);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const atCap = destinations.length >= MAX_WEBHOOK_DESTINATIONS;

  async function refresh() {
    const res = await fetchWebhooks();
    if (!ensureOk(res, toast)) return;
    setDestinations(res.data.items);
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-2">
          <Breadcrumbs trail={[{ label: "Settings", href: webRoutes.settings }]} />
          <h1 className="text-2xl font-bold text-foreground">Webhooks</h1>
          {destinations.length === 0 && (
            <p className="text-sm text-muted">Where your sheets send signed deliveries.</p>
          )}
        </div>
        {!atCap && (
          <Button onClick={() => setDrawerOpen(true)}>
            <Plus aria-hidden className="mr-1.5 h-4 w-4" />
            Add destination
          </Button>
        )}
      </div>

      {atCap && (
        <p className="text-sm text-warning">
          This account has {MAX_WEBHOOK_DESTINATIONS} destinations, the most it can hold. Delete one to add another.
        </p>
      )}

      {destinations.length === 0 ? (
        <EmptyState title={EMPTY_TITLE} subtitle={EMPTY_SUBTITLE}>
          <Button size="sm" onClick={() => setDrawerOpen(true)}>
            Add your first destination
          </Button>
        </EmptyState>
      ) : (
        <ul className="space-y-3">
          {destinations.map((destination) => (
            <DestinationCard key={destination.id} destination={destination} />
          ))}
        </ul>
      )}

      <AddDestinationDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onCreated={(destination) => setDestinations((prev) => [destination, ...prev])}
        onFull={() => void refresh()}
      />
    </>
  );
}
