"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card } from "@bower/ui";
import { SIGNAL_FEED, hostedSource, isKnownSignalType, signalFeed, type SignalDetail, type SignalsCatalog } from "@bower/api";

import { ReconnectDialog, SignalBanner } from "../../_components/signals";
import { DELETE_ACTION, UNKNOWN_FEED_NOTE, noSetupNote } from "./copy";
import { DeleteSignalDialog } from "./delete-signal-dialog";
import { SignalHeader } from "./signal-header";
import { SignalForm } from "./signal-form";
import { WebhookConnection } from "./webhook-connection";

/** A signal's settings: what stops it (its banner, with Reconnect), then
 * the form editing a hosted signal's saved setup, or a custom signal's
 * webhook connection. One fed by a source kind this bundle cannot name,
 * one whose listener is gone, and one this bundle has no form for offer
 * only their delete. The catalog is read
 * for a hosted signal alone (null otherwise), so a custom signal's page
 * never waits on the signals service. */
export function SignalSettings({ log, catalog }: { log: SignalDetail; catalog: SignalsCatalog | null }) {
  const router = useRouter();
  const [askingReconnect, setAskingReconnect] = useState(false);
  const [askingDelete, setAskingDelete] = useState(false);
  const signal = { id: log.sheet.id, label: log.sheet.label, rowCount: log.sheet.row_count };

  const banner = (
    <SignalBanner settingsHref={null} log={log} onReconnect={() => setAskingReconnect(true)} />
  );
  const reconnect = (
    <ReconnectDialog
      open={askingReconnect}
      onClose={() => setAskingReconnect(false)}
      refresh={async () => {
        router.refresh();
        return true;
      }}
      // The banner and its button go with the status; the page's heading
      // stays.
      onReconnected={() => document.querySelector<HTMLElement>("h1")?.focus()}
    />
  );
  const remove = <DeleteSignalDialog open={askingDelete} onClose={() => setAskingDelete(false)} signal={signal} />;

  // The catalog offers only the types this bundle knows, so an offered
  // type is the one test the form needs; the note says which part failed.
  const feed = signalFeed(log);
  const hosted = hostedSource(log);
  if (hosted !== undefined && catalog === null) throw new Error("A hosted signal's settings need the catalog.");
  const offered = hosted && catalog?.types.find((each) => each.type === log.type);
  const listener = log.listener;
  // On the feed, not the type: what there is to set up is a source's
  // question (the list's type column asks the type).
  if (feed === SIGNAL_FEED.CUSTOM) {
    return (
      <>
        <SignalHeader
          title={log.sheet.label}
          sheetId={log.sheet.id}
          actions={
            <Button variant="ghost" size="sm" onClick={() => setAskingDelete(true)} className="text-danger">
              {DELETE_ACTION}
            </Button>
          }
        />
        {banner}
        <Card className="p-4">
          <WebhookConnection signalId={log.sheet.id} />
        </Card>
        {remove}
        {reconnect}
      </>
    );
  }
  if (hosted === undefined || catalog === null || offered === undefined || listener === null) {
    const note =
      hosted === undefined
        ? UNKNOWN_FEED_NOTE
        : noSetupNote({ listenerGone: listener === null, type: log.type, known: isKnownSignalType(log.type) });
    return (
      <>
        <SignalHeader title={log.sheet.label} sheetId={log.sheet.id} />
        {banner}
        <Card className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
          <p className="min-w-0 text-muted">{note}</p>
          <Button variant="ghost" onClick={() => setAskingDelete(true)} className="text-danger">
            {DELETE_ACTION}
          </Button>
        </Card>
        {remove}
        {reconnect}
      </>
    );
  }

  return (
    <>
      <SignalForm
        catalog={catalog}
        saved={{ ...signal, type: offered.type, listener }}
        notice={banner}
        onDelete={() => setAskingDelete(true)}
      />
      {remove}
      {reconnect}
    </>
  );
}
