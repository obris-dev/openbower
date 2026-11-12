"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button, Card, ConfirmDialog, EmptyState, LocalTime, TIME_FORMAT, useToast } from "@bower/ui";
import { deleteLog, fetchLogsPage, webRoutes, type LogSummary } from "@bower/api";

import { ensureOk, redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { ConfirmDelete } from "../../_components/confirm-delete";
import { LinkButton } from "../../../_components/link-button";
import {
  CREATE_ACTION,
  DELETE_QUESTION,
  DELETED_TOAST,
  EMPTY_SUBTITLE,
  EMPTY_TITLE,
  INTRO,
  NOT_YET_READ,
  TITLE,
  deleteConsequence,
  signalType,
} from "./copy";
import { SignalRowActions } from "./row-actions";
import { SignalStatus } from "./status";

/** The delete being asked about: which signal, whether it is in flight,
 * and the server's refusal when it answered one (rendered in the dialog,
 * which stays open). Kept after the dialog closes, so its form still
 * fills the panel through the leave transition. */
type Deleting = { signal: LogSummary; busy: boolean; refusal: string | null };
// Where focus goes once a delete's dialog has closed: the actions of the
// row now in the deleted one's place, or Create signal when none is left.
const CREATE_TARGET = "create";

/** The signals a person set up, newest first, in the directory table
 * idiom: what each one is, where it stands, and the two ways in (its
 * settings, and the sheet it fills). */
export function SignalsList({
  initialSignals,
  initialNextCursor,
}: {
  initialSignals: LogSummary[];
  initialNextCursor: string | null;
}) {
  const toast = useToast();
  const router = useRouter();
  const [signals, setSignals] = useState(initialSignals);
  const [nextCursor, setNextCursor] = useState(initialNextCursor);
  const [loadingMore, setLoadingMore] = useState(false);
  const [deleting, setDeleting] = useState<Deleting | null>(null);
  const [asking, setAsking] = useState(false);
  const [refocus, setRefocus] = useState<string | null>(null);

  // The dialog hands focus back to the button that opened it, which left
  // with its row; once it has closed, focus goes where the row was.
  useEffect(() => {
    if (asking || refocus === null) return;
    const frame = requestAnimationFrame(() => {
      const row = document.querySelector<HTMLElement>(`[data-signal-actions="${refocus}"]`);
      const target = row ?? document.querySelector<HTMLElement>("[data-create-signal]");
      target?.focus();
      setRefocus(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [asking, refocus]);

  function ask(signal: LogSummary) {
    setDeleting({ signal, busy: false, refusal: null });
    setAsking(true);
  }

  function stopAsking() {
    if (deleting?.busy) return;
    setAsking(false);
  }
  // Nothing loaded is not nothing there: a person can delete every loaded
  // signal while more wait behind the cursor.
  const none = signals.length === 0 && nextCursor === null;

  async function confirmDelete() {
    if (!deleting || deleting.busy) return;
    const { signal } = deleting;
    setDeleting({ signal, busy: true, refusal: null });
    const res = await deleteLog(signal.sheet.id);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      setDeleting({ signal, busy: false, refusal: res.message });
      return;
    }
    const index = signals.findIndex((each) => each.sheet.id === signal.sheet.id);
    const remaining = signals.filter((each) => each.sheet.id !== signal.sheet.id);
    const neighbour = remaining[index] ?? remaining[index - 1];
    setDeleting({ signal, busy: false, refusal: null });
    setAsking(false);
    setRefocus(neighbour ? neighbour.sheet.id : CREATE_TARGET);
    setSignals(remaining);
    toast.success(DELETED_TOAST, signal.sheet.label);
    router.refresh();
    if (remaining.length === 0 && nextCursor !== null) await loadMore();
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    const res = await fetchLogsPage(nextCursor);
    setLoadingMore(false);
    if (!ensureOk(res, toast)) return;
    setSignals((prev) => [...prev, ...res.data.items]);
    setNextCursor(res.data.next_cursor);
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">{TITLE}</h1>
          {none && <p className="mt-1 text-sm text-muted">{INTRO}</p>}
        </div>
        <LinkButton href={webRoutes.signalNew} data-create-signal>
          {CREATE_ACTION}
        </LinkButton>
      </div>

      {none ? (
        <EmptyState title={EMPTY_TITLE} subtitle={EMPTY_SUBTITLE}>
          <LinkButton size="sm" href={webRoutes.signalNew}>
            {CREATE_ACTION}
          </LinkButton>
        </EmptyState>
      ) : (
        <Card className="p-0">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-faint">
                <th className="w-full py-3 pl-4 pr-4 font-medium">Name</th>
                <th className="hidden px-4 py-3 font-medium sm:table-cell">Type</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="hidden px-4 py-3 text-right font-medium md:table-cell">Rows</th>
                <th className="hidden px-4 py-3 font-medium md:table-cell">Last read</th>
                <th className="py-3 pl-2 pr-3" aria-label="Actions" />
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {signals.map((signal) => (
                <tr key={signal.sheet.id} className="align-middle">
                  <td className="w-full max-w-0 py-2.5 pl-4 pr-4">
                    <Link
                      href={webRoutes.sheet(signal.sheet.id)}
                      title={signal.sheet.label}
                      className="block truncate font-medium text-foreground hover:text-signal"
                    >
                      {signal.sheet.label}
                    </Link>
                  </td>
                  <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted sm:table-cell">
                    {signalType(signal.signal)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-2.5">
                    <SignalStatus status={signal.status} />
                  </td>
                  <td className="hidden whitespace-nowrap px-4 py-2.5 text-right tabular-nums text-muted md:table-cell">
                    {signal.sheet.row_count.toLocaleString("en-US")}
                  </td>
                  <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted md:table-cell">
                    {signal.last_polled_at ? (
                      <LocalTime iso={signal.last_polled_at} format={TIME_FORMAT.MINUTE} />
                    ) : (
                      NOT_YET_READ
                    )}
                  </td>
                  <td className="whitespace-nowrap py-2.5 pl-2 pr-3 text-right">
                    <SignalRowActions
                      id={signal.sheet.id}
                      label={signal.sheet.label}
                      editHref={webRoutes.signal(signal.sheet.id)}
                      viewHref={webRoutes.sheet(signal.sheet.id)}
                      onDelete={() => ask(signal)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {nextCursor && (
            <div className="border-t border-hairline p-3 text-center">
              <Button size="sm" variant="ghost" loading={loadingMore} onClick={() => void loadMore()}>
                Load more
              </Button>
            </div>
          )}
        </Card>
      )}

      <ConfirmDialog open={asking} onClose={stopAsking}>
        {deleting && (
          <ConfirmDelete
            layout="dialog"
            question={DELETE_QUESTION}
            consequence={deleteConsequence(deleting.signal.sheet.row_count)}
            busy={deleting.busy}
            refusal={deleting.refusal}
            onCancel={stopAsking}
            onDelete={() => void confirmDelete()}
          />
        )}
      </ConfirmDialog>
    </>
  );
}
