"use client";

import { useEffect, useState } from "react";

import { Button, cn, CopyButton, LocalTime, Skeleton, TIME_FORMAT, TouchTarget } from "@bower/ui";
import { X } from "lucide-react";
import type { ApiResult, CellDetail, RenderableSheetRow, SheetColumn } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

import { cellHref, cellLinkIsExternal } from "../../../../_components/cell-link";
import { cellMoment } from "../../../../_components/cell-moment";
import { cellNumber } from "../../../../_components/cell-number";
import { cellCauseSentence, type SearchProviderChoice } from "../fill";
import type { RowOutcome } from "../use-rows";
import { ConfidenceMark } from "./confidence-mark";
import {
  DETAIL_FAILED,
  EMPTY_CELL,
  NOT_ON_SHEET,
  UNCONFIRMED_LABEL,
  USE_ANSWER,
  YOU_SET_THIS,
} from "./lib/confidence";
import { cellVersion, detailsView } from "./lib/details-view";

export type DetailsTarget = { row: RenderableSheetRow; column: SheetColumn };

type Loaded = { status: "loading" } | { status: "ready"; detail: CellDetail } | { status: "failed" };

// A held arrow key passes cells faster than this, so only the cell it
// stops on is read.
const DETAIL_SETTLE_MS = 150;

/** The place to read one cell in full, docked beside the grid (Clay's
 * cell details): NOT a modal, so the sheet stays live beside it and the
 * next cell is one press away. The grid keeps every row to one line; a
 * long value, or why an agent answered what it did, is read here. Any
 * plain or AI cell: the whole value (wrapped, selectable, copyable);
 * for an agent's cell, how sure its run was and its full reasoning in
 * the open (the body scrolls), read when the cell is opened; for an
 * unconfirmed answer, Use answer, the person's own write. Below sm it
 * covers the sheet, which has no room for both. */
export function CellDetailsPanel({
  target,
  columns,
  searchProvider,
  onClose,
  detail,
  write,
}: {
  target: DetailsTarget;
  columns: SheetColumn[];
  searchProvider: SearchProviderChoice;
  onClose: () => void;
  detail: (rowId: string, columnKey: string) => Promise<ApiResult<CellDetail>>;
  write: (rowId: string, columnKey: string, value: string) => Promise<RowOutcome>;
}) {
  const view = detailsView(columns, target.column, target.row);
  const unconfirmed = view.unconfirmed;
  const [using, setUsing] = useState(false);

  async function use() {
    setUsing(true);
    const outcome = await write(target.row.id, target.column.key, unconfirmed);
    // On success the row swaps in with the value and the footer goes;
    // on a refusal the sheet's toast says why and the answer stays.
    if (!outcome.ok) setUsing(false);
  }

  return (
    <aside
      aria-label={`${target.column.label} details`}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
      className="flex min-h-0 w-full flex-col border-l border-hairline bg-surface max-sm:absolute max-sm:inset-0 max-sm:z-30 sm:w-96 sm:flex-none"
    >
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-hairline px-4">
        <h2 className="min-w-0 truncate text-sm font-semibold text-foreground">{target.column.label}</h2>
        <button
          type="button"
          onClick={onClose}
          className="relative shrink-0 rounded-md p-1.5 text-faint transition-colors hover:bg-wash hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600"
        >
          <TouchTarget>
            <span className="sr-only">Close</span>
            <X aria-hidden className="h-4 w-4" />
          </TouchTarget>
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 text-sm">
        {view.rowLabel && <p className="-mt-1 text-faint">{view.rowLabel}</p>}
        <CellValue column={target.column} value={view.value} unconfirmed={unconfirmed} />
        {view.agent && (
          <AgentAccount
            rowId={target.row.id}
            columnKey={target.column.key}
            version={cellVersion(target.row, target.column.key)}
            cause={target.row.states?.[target.column.key] ? cellCauseSentence(target.row.states[target.column.key]!, searchProvider) : null}
            filled={Boolean(view.value)}
            detail={detail}
          />
        )}
      </div>
      {unconfirmed && (
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-hairline px-4 py-3">
          <p className="text-sm text-faint">{NOT_ON_SHEET}</p>
          <Button variant="secondary" size="sm" loading={using} onClick={() => void use()}>
            {USE_ANSWER}
          </Button>
        </div>
      )}
    </aside>
  );
}

/** The value in full. A URL is a link, an unconfirmed one included:
 * opening it is how a person checks the answer before using it. A
 * moment reads in the reader's zone, an amount with grouped digits; the
 * copy is the stored value either way. */
function CellValue({ column, value, unconfirmed }: { column: SheetColumn; value: string; unconfirmed: string }) {
  const shown = value || unconfirmed;
  if (!shown) return <p className="text-faint">{EMPTY_CELL}</p>;
  const href = cellHref(column.type, shown);
  const moment = cellMoment(column.type, shown);
  const number = cellNumber(column.type, shown);
  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">
        {unconfirmed && <p className="mb-1 text-xs text-faint">{UNCONFIRMED_LABEL}</p>}
        {href ? (
          <a
            href={href}
            {...(cellLinkIsExternal(column.type) ? { target: "_blank", rel: "noreferrer" } : {})}
            className={cn(
              "underline decoration-hairline underline-offset-2 hover:text-signal hover:decoration-current [overflow-wrap:anywhere]",
              unconfirmed ? "text-muted" : "text-foreground",
            )}
          >
            {shown}
          </a>
        ) : moment ? (
          <LocalTime iso={moment} format={TIME_FORMAT.STAMP} className="text-foreground" title={moment} />
        ) : (
          <p
            className={cn(
              "whitespace-pre-wrap [overflow-wrap:anywhere]",
              number && "tabular-nums",
              unconfirmed ? "text-muted" : "text-foreground",
            )}
          >
            {number ?? shown}
          </p>
        )}
      </div>
      <CopyButton value={shown} label="Copy value" iconOnly />
    </div>
  );
}

/** What the cell's record says about it, loaded on open: how sure the
 * model was and its full reasoning (the panel's body scrolls), or the
 * blank's cause when it answered nothing. */
function AgentAccount({
  rowId,
  columnKey,
  version,
  cause,
  filled,
  detail,
}: {
  rowId: string;
  columnKey: string;
  /** What the cell holds: a change (a write, a landed run) re-reads it. */
  version: string;
  cause: string | null;
  filled: boolean;
  detail: (rowId: string, columnKey: string) => Promise<ApiResult<CellDetail>>;
}) {
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const res = await detail(rowId, columnKey);
      if (cancelled || redirectIfUnauthenticated(res)) return;
      setLoaded(res.status === "ok" ? { status: "ready", detail: res.data } : { status: "failed" });
    }
    const timer = setTimeout(() => void load(), DETAIL_SETTLE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [detail, rowId, columnKey, version]);

  if (loaded.status === "loading") {
    return (
      <div className="space-y-2 border-t border-hairline pt-5">
        <Skeleton className="h-3 w-40" />
        <Skeleton className="h-3 w-72" />
        <Skeleton className="h-3 w-56" />
      </div>
    );
  }
  // A failed read still has the blank's cause, if any.
  if (loaded.status === "failed") {
    return cause ? <Account cause={cause} /> : filled ? null : <p className="border-t border-hairline pt-5 text-muted">{DETAIL_FAILED}</p>;
  }
  const { level, reason, state } = loaded.detail;
  if (!level && !reason) return state === "filled" && filled ? <Account cause={YOU_SET_THIS} /> : cause ? <Account cause={cause} /> : null;
  return (
    <div className="space-y-3 border-t border-hairline pt-5">
      {level && <ConfidenceMark level={level} />}
      {reason && <p className="whitespace-pre-wrap text-foreground [overflow-wrap:anywhere]">{reason}</p>}
    </div>
  );
}

function Account({ cause }: { cause: string }) {
  return <p className="border-t border-hairline pt-5 text-muted">{cause.endsWith(".") ? cause : `${cause}.`}</p>;
}
