"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, X } from "lucide-react";
import { Button, Input, Spinner, useToast } from "@bower/ui";
import { saveRunAsList, webRoutes } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";

type SavePhase =
  | { at: "invite" }
  | { at: "naming" }
  | { at: "creating"; label: string; count: number }
  | { at: "landed"; label: string; listId: string; rows: number };

/** Save the results as a list: ONE header surface owning the whole
 * lifecycle (invite -> name -> creating -> a doorway to the sheet). The
 * count is not asked here: the results limit and the confidence cutoff
 * already decided what "the results" are; saving keeps that number. */
export function SaveAsList({ runId, count, exclude }: { runId: string; count: number; exclude: string[] }) {
  const toast = useToast();
  const [phase, setPhase] = useState<SavePhase>({ at: "invite" });
  const [label, setLabel] = useState("");

  async function save() {
    const name = label.trim();
    if (!name) return;
    setPhase({ at: "creating", label: name, count });
    const res = await saveRunAsList(runId, { label: name, limit: count, exclude });
    if (!ensureOk(res, toast, { title: "Save failed" })) {
      setPhase({ at: "naming" });
      return;
    }
    setLabel("");
    setPhase({ at: "landed", label: res.data.label, listId: res.data.id, rows: res.data.row_count });
  }

  if (phase.at === "invite") {
    return (
      <Button size="sm" variant="secondary" onClick={() => setPhase({ at: "naming" })}>
        Save as list
      </Button>
    );
  }

  if (phase.at === "naming") {
    return (
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Input
          autoFocus
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="List name"
          aria-label="List name"
          className="w-44"
        />
        <Button size="sm" type="submit" disabled={!label.trim()}>
          Save
        </Button>
        <Button size="sm" variant="ghost" type="button" onClick={() => setPhase({ at: "invite" })}>
          Cancel
        </Button>
      </form>
    );
  }

  if (phase.at === "creating") {
    return (
      <span className="flex items-center gap-2 text-sm text-muted">
        <Spinner className="text-signal" />
        <span className="flex min-w-0 items-baseline gap-1">
          <span>Creating</span>
          <span title={phase.label} className="max-w-48 truncate font-medium text-foreground">
            &ldquo;{phase.label}&rdquo;
          </span>
          <span className="whitespace-nowrap">| snapshotting {phase.count.toLocaleString("en-US")} rows…</span>
        </span>
      </span>
    );
  }

  return (
    <span className="flex items-center gap-2 text-sm">
      <Check aria-hidden className="h-4 w-4 text-green-600" />
      <span className="flex min-w-0 items-baseline gap-1 text-muted">
        <span title={phase.label} className="max-w-48 truncate font-medium text-foreground">
          &ldquo;{phase.label}&rdquo;
        </span>
        <span className="whitespace-nowrap">is ready | {phase.rows.toLocaleString("en-US")} rows</span>
      </span>
      <Link href={webRoutes.list(phase.listId)} className="font-medium text-signal hover:underline">
        Open list
      </Link>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => setPhase({ at: "invite" })}
        className="rounded p-0.5 text-faint hover:text-foreground"
      >
        <X aria-hidden className="h-3.5 w-3.5" />
      </button>
    </span>
  );
}
