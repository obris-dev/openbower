"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card } from "@bower/ui";
import { hostedSource, isKnownSignal, type SignalDetail, type SignalsCatalog } from "@bower/api";

import { ReconnectDialog, SignalBanner } from "../../_components/signals";
import { CUSTOM_NOTE, DELETE_ACTION, noSetupNote } from "./copy";
import { DeleteSignalDialog } from "./delete-signal-dialog";
import { SignalHeader } from "./signal-header";
import { SignalForm } from "./signal-form";

/** A signal's settings: what stops it (its banner, with Reconnect), then
 * the form editing its saved setup. A signal whose listener is gone, or one
 * this bundle has no form for, offers only its delete. */
export function SignalSettings({ log, catalog }: { log: SignalDetail; catalog: SignalsCatalog }) {
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

  // The catalog offers only the signals this bundle knows, so an offered
  // kind is the one test the form needs; the note says which part failed.
  // The form edits a hosted signal's listener; a custom signal has none.
  const hosted = hostedSource(log);
  const offered = hosted && catalog.signals.find((each) => each.signal === hosted.signal);
  const listener = log.listener;
  if (hosted === undefined || offered === undefined || listener === null) {
    const note =
      hosted === undefined
        ? CUSTOM_NOTE
        : noSetupNote({ listenerGone: listener === null, signal: hosted.signal, known: isKnownSignal(hosted.signal) });
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
        saved={{ ...signal, signal: offered.signal, listener }}
        notice={banner}
        onDelete={() => setAskingDelete(true)}
      />
      {remove}
      {reconnect}
    </>
  );
}
