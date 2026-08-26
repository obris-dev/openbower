"use client";

import { useState } from "react";
import { FlaskConical, X } from "lucide-react";
import { Button, Input, Label, Spinner, useToast } from "@bower/ui";
import { fetchListRows, TEST_ROW_MAX_KEYS, type AgentOutput, type AgentTestResult, type ListSummary } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { cellHref, cellLinkIsExternal } from "../../../_components/cell-link";
import { CompactSelect } from "../../../_components/compact-select";
import { sheetsTruncatedNote } from "../../../_components/agent-config/copy";
import { outputKey } from "../../../_components/agent-config";

/** The test bench's INPUTS AND RESULTS: hand-fed values for the
 * prompt's {{tokens}}, every one removable (removal strips the token,
 * the chip-toggle semantics), the cells a run would write, the
 * searches with each query's diagnosis, and the evidence the model
 * saw. The tokens are the ONLY inputs: every tool derives its context
 * from the rendered prompt (use-time interpretation; no magic row
 * keys). The Test ACTION lives in the pinned footer with the other
 * primary verbs. */
export function TestBench({
  inputKeys,
  lists,
  listsLoading = false,
  listsTruncated = false,
  testRow,
  onTestRow,
  result,
  busy,
  sourceListId,
  onSourceList,
  onRemoveKey,
  toolsOn,
  outputs,
  supportFollowup,
}: {
  inputKeys: string[];
  lists: ListSummary[];
  listsLoading?: boolean;
  listsTruncated?: boolean;
  testRow: Record<string, string>;
  onTestRow: (next: Record<string, string>) => void;
  result: AgentTestResult | null;
  busy: boolean;
  sourceListId: string;
  onSourceList: (id: string) => void;
  onRemoveKey: (key: string) => void;
  toolsOn: boolean;
  outputs: AgentOutput[];
  supportFollowup?: string;
}) {
  const toast = useToast();
  const [borrowing, setBorrowing] = useState(false);

  async function fillFromList() {
    if (!sourceListId || borrowing) return;
    setBorrowing(true);
    const res = await fetchListRows(sourceListId, { limit: 1 });
    setBorrowing(false);
    if (!ensureOk(res, toast)) return;
    const first = res.data.items[0];
    if (!first) {
      toast.error("That sheet has no rows to borrow.");
      return;
    }
    // Only the RENDERED inputs: copying every column would stash
    // invisible values that outlive their fields. The contract types
    // row values as strings; no re-coercion.
    const next = { ...testRow };
    for (const key of inputKeys) {
      const value = first.data[key];
      if (value !== undefined) next[key] = value;
    }
    onTestRow(next);
  }

  const sourceList = lists.find((l) => l.id === sourceListId) ?? null;
  // Normalized once: a localStorage DRAFT can hold a result from before
  // this field existed, so absence must degrade, never crash.
  const searches = result?.searches ?? [];
  const failedSearches = searches.filter((s) => s.failed).length;
  const totalHits = searches.reduce((acc, s) => acc + s.hits, 0);
  const emptyCells = result !== null && Object.keys(result.cells).length === 0;
  // Rendering follows the DECLARED type through the SAME cell-link
  // module the sheet renders with: a value must not link here and sit
  // flat there (or vice versa).
  const typeByKey = new Map(outputs.map((o) => [outputKey(o), o.type]));

  return (
    <div className="space-y-3 rounded-lg border border-hairline p-4">
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-faint">
        <FlaskConical aria-hidden className="h-3.5 w-3.5" />
        Test on one row
      </p>
      {inputKeys.length === 0 ? (
        <p className="text-xs text-muted">
          Reference a column in the prompt (type {"{{domain}}"}) to feed test values.
        </p>
      ) : (
        <>
          {inputKeys.filter((key) => (testRow[key] ?? "") !== "").length > TEST_ROW_MAX_KEYS && (
            // Judged on FILLED inputs (the payload carries only
            // those, in prompt order); the rest render blank, so
            // silence here would be an undiagnosed blank variable.
            <p className="text-xs text-warning">
              Only the first {TEST_ROW_MAX_KEYS} filled inputs run in a test (in prompt order); the rest render
              blank.
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <CompactSelect
              value={sourceListId}
              onChange={(e) => onSourceList(e.target.value)}
              disabled={listsLoading}
              aria-label="Borrow test values from list"
            >
              <option value="">{listsLoading ? "Loading sheets…" : "Borrow values from…"}</option>
              {lists.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </CompactSelect>
            {sourceList && (
              <Button type="button" variant="ghost" size="sm" loading={borrowing} onClick={() => void fillFromList()}>
                Fill from its first row
              </Button>
            )}
            {listsTruncated && <span className="text-xs text-faint">{sheetsTruncatedNote(lists.length)}</span>}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {inputKeys.map((key) => (
              <div key={key}>
                <div className="flex items-center justify-between">
                  <Label htmlFor={`test-${key}`}>{key}</Label>
                  <button
                    type="button"
                    onClick={() => onRemoveKey(key)}
                    aria-label={`Remove ${key} from the prompt and the test`}
                    className="rounded p-0.5 text-faint hover:text-danger"
                  >
                    <X aria-hidden className="h-3.5 w-3.5" />
                  </button>
                </div>
                <Input
                  id={`test-${key}`}
                  value={testRow[key] ?? ""}
                  onChange={(e) => onTestRow({ ...testRow, [key]: e.target.value })}
                />
              </div>
            ))}
          </div>
        </>
      )}
      {busy && (
        <span className="flex items-center gap-2 text-xs text-muted">
          <Spinner className="h-3.5 w-3.5" />
          searching and thinking…
        </span>
      )}
      {result !== null && !busy && (
        <div className="rounded-lg bg-wash px-3 py-2 text-sm">
          {Object.keys(result.cells).length > 0 ? (
            <div className="divide-y divide-hairline">
              {Object.entries(result.cells).map(([key, value]) => {
                const type = typeByKey.get(key) ?? "text";
                const href = cellHref(type, value);
                return (
                  <div
                    key={key}
                    className="flex flex-col gap-0.5 py-1.5 first:pt-0 last:pb-0 sm:flex-row sm:items-baseline sm:gap-2"
                  >
                    <span className="min-w-0 truncate font-mono text-xs leading-6 text-faint sm:shrink-0">{key}</span>
                    {href ? (
                      <a
                        href={href}
                        {...(cellLinkIsExternal(type) ? { target: "_blank", rel: "noreferrer" } : {})}
                        className="min-w-0 truncate text-signal hover:underline"
                      >
                        {value}
                      </a>
                    ) : (
                      <span
                        className="line-clamp-3 min-w-0 font-medium text-foreground [overflow-wrap:anywhere]"
                        title={value}
                      >
                        {value}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <span className="text-muted">
              Empty: no evidence found or no confident answer (the cells would stay blank).
            </span>
          )}
          {/* A broken provider must not read as a bad agent: failures
              get strong guidance, an all-empty pass a soft one. */}
          {emptyCells && toolsOn && searches.length === 0 && (
            <p className="mt-2 text-xs text-warning">
              No searches ran: the model never used its tools (try a more capable model), or search isn&rsquo;t set
              up on this deployment (see Tools).
            </p>
          )}
          {failedSearches > 0 && (
            <p className="mt-2 text-xs text-warning">
              {failedSearches} of {searches.length} searches failed: the search provider errored (rate limit,
              outage, bad credentials, or a drained DataForSEO balance)
              {/* The follow-up clause renders only when the fact
                  arrived; a hedged fallback would re-ship exactly
                  what this field ends. */}
              {supportFollowup ? `; if it keeps happening, ${supportFollowup}.` : "."}
            </p>
          )}
          {emptyCells && failedSearches === 0 && searches.length > 0 && totalHits === 0 && (
            <p className="mt-2 text-xs text-muted">
              Every search returned nothing. If queries that match on Google keep returning nothing here, the free
              search provider may be rate-limiting; DataForSEO (pay as you go, a deployment setting) gives
              Google-grade results and dedicated throughput.
            </p>
          )}
          {searches.length > 0 && (
            <details className="mt-2 border-t border-hairline pt-2">
              <summary className="cursor-pointer text-xs text-faint hover:text-foreground">
                Searches ({searches.length})
              </summary>
              <ul className="mt-1.5 space-y-1">
                {searches.map((search, index) => (
                  <li key={index} className="truncate text-xs text-muted" title={search.query}>
                    {search.failed ? "failed" : `${search.hits} hits`} | {search.query}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {result.evidence.length > 0 && (
            <details className="mt-2 border-t border-hairline pt-2">
              <summary className="cursor-pointer text-xs text-faint hover:text-foreground">
                Evidence the model saw ({result.evidence.length})
              </summary>
              <ul className="mt-1.5 space-y-1">
                {result.evidence.map((line, index) => (
                  <li key={index} className="truncate text-xs text-muted" title={line}>
                    {line}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
