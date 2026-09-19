"use client";

import type { WebhookCellState as Word } from "@bower/api";

import { CauseMark } from "../fill";
import { CELL_FAILED, CELL_FAILED_CAUSE, CELL_FAILED_FACT, CELL_SENT, CELL_WAITING } from "./copy";

const WORDS: Record<Word, string> = { waiting: CELL_WAITING, sent: CELL_SENT, failed: CELL_FAILED };

/** A Send webhook column's cell: the column holds no value, so the
 * word IS the cell, in the settled-word idiom of an AI cell (quiet,
 * faint, never louder than a real value). A failed send keeps the
 * same quiet word but opens the fuller sentence on tap, click, and
 * focus (the CauseMark floor), pointing at the delivery log, since the
 * error itself is not on this wire. No word (the row has never been
 * due for this column) renders nothing. */
export function WebhookCellState({ word }: { word: Word | undefined }) {
  if (word === undefined) return null;
  if (word === "failed") {
    return (
      <CauseMark cause={CELL_FAILED_CAUSE} fact={CELL_FAILED_FACT}>
        <span aria-hidden className="text-xs text-warning">
          {WORDS[word]}
        </span>
      </CauseMark>
    );
  }
  return <span className="text-xs text-faint">{WORDS[word]}</span>;
}
