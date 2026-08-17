"use client";

import { useMemo } from "react";

import { ChevronRight } from "lucide-react";
import { Button, Card, Spinner } from "@bower/ui";
import { normalizeDomain, type LookalikeListResponse } from "@bower/api";

import { GhostTable } from "./ghost-table";
import { ResultRows } from "./result-rows";
import { SaveAsList } from "./save-as-list";

// A real, runnable cohort (verified in the index, embeddings included):
// the empty state's invitation is one click that fills the form and
// searches, which teaches the input by doing rather than describing.
// Deliberately far from any vertical the product's own copy leans on.
export const EXAMPLE_SEEDS = "marriott.com, hilton.com, hyatt.com";

/** The results surface: ONE card owning the whole outcome. The header
 * states what was found ("Found N similar matches", the engine's
 * confidence cutoff) and carries Save as list; the meta line holds the
 * caveats; the table lives in a bounded scroll region below. Rendered
 * inside a persistent aria-live region owned by the page, so the arrival
 * of results is announced. */
export function ResultsPanel({
  searching,
  computing,
  result,
  excluded,
  limitRaw,
  loadingMore,
  onLoadMore,
  onRunExample,
}: {
  searching: boolean;
  computing: boolean;
  result: LookalikeListResponse | null;
  excluded: Set<string>;
  limitRaw: string;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRunExample: () => void;
}) {
  // The confidence cutoff: each group cut at its own decay boundary. It
  // is the headline AND the save-as-list default; a typed limit caps it.
  const groups = result?.groups ?? [];
  const confident = groups.reduce((acc, g) => acc + (g.elbow_rank ?? 0), 0);
  const total = result?.result_count ?? 0;
  const found = confident > 0 ? confident : total;
  const limit = limitRaw ? Math.max(1, Number(limitRaw)) : null;
  const cutoff = limit === null ? found : Math.min(found, limit);
  const runId = result?.run_id ?? null;
  // Normalized like the server-side save filter (the table and the
  // saved sheet must exclude the same rows), memoized because
  // normalizeDomain parses a URL per item and this runs per keystroke.
  // Above the early returns: hooks run unconditionally.
  const visibleItems = useMemo(
    () => (result?.items ?? []).filter((item) => !excluded.has(normalizeDomain(item.company.domain))),
    [result, excluded],
  );
  if (searching) {
    // The status line occupies the header slot where "Found N similar
    // matches" will land; the pulsing ghost rows are the table filling.
    return (
      <Card className="p-6">
        <div className="mb-4 flex items-center gap-3 text-sm text-muted">
          <Spinner className="text-signal" />
          <span>{computing ? "Building your list; a first search can take a few minutes." : "Searching…"}</span>
        </div>
        <GhostTable pulsing />
      </Card>
    );
  }
  if (!result) {
    // Awaiting a question: static ghost rows fading toward the one action.
    return (
      <Card className="p-6">
        <GhostTable pulsing={false} />
        <div className="mt-4 text-center">
          <Button variant="ghost" size="sm" onClick={onRunExample}>
            Try an example: {EXAMPLE_SEEDS}
          </Button>
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-6">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-baseline sm:justify-between">
        <h2 className="text-lg font-semibold text-foreground">
          Found {found.toLocaleString("en-US")} similar matches
        </h2>
        {runId && cutoff > 0 && (
          <div className="sm:shrink-0">
            <SaveAsList runId={runId} count={cutoff} exclude={[...excluded]} />
          </div>
        )}
      </div>
      {/* Meta only when there is something to explain: a binding cap, or
          no defensible cutoff. The normal case needs no caption. */}
      {(confident === 0 || (limit !== null && limit < confident)) && (
        <p className="-mt-3 mb-4 text-xs text-muted">
          {confident === 0
            ? "No natural cutoff in the data for these seeds; showing everything above the similarity floor."
            : `The data supports ${confident.toLocaleString("en-US")}; capped at your ${limit?.toLocaleString("en-US")}.`}
        </p>
      )}
      {result.unresolved_domains.length > 0 && (
        <p className="mb-3 text-xs text-muted">
          Not in the index yet: {result.unresolved_domains.join(", ")}
        </p>
      )}
      {result.outlier_domains.length > 0 && (
        <p className="mb-3 text-xs text-muted">
          Set aside (unlike your other seeds): {result.outlier_domains.join(", ")}
        </p>
      )}
      {visibleItems.length === 0 ? (
        <p className="text-sm text-muted">No similar sites found for these seeds.</p>
      ) : (
        <div className="max-h-[65vh] overflow-y-auto overscroll-contain pr-3">
          {/* pr gives the overlay scrollbar its own gutter so it never
              sits on the Score column's numerals. */}
          {groups.length > 1 ? (
            // One collapsible section per group: top results from each,
            // inline; the header carries the group's seeds and boundary.
            groups.map((g, i) => {
              const label = g.label || `Group ${i + 1}`;
              const rows = visibleItems.filter((item) => item.group === label);
              return (
                <details key={label} open className="group/section mb-2 last:mb-0">
                  <summary className="flex cursor-pointer items-start gap-2 rounded bg-wash px-3 py-2 text-sm">
                    {/* Points right collapsed, down open: the header reads
                        as clickable. */}
                    <ChevronRight
                      aria-hidden
                      className="mt-1 h-4 w-4 shrink-0 text-muted group-open/section:rotate-90"
                    />
                    {/* Stacks on mobile; single baseline row from sm up. */}
                    <span className="flex min-w-0 flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
                      <span className="font-medium text-foreground">{label}</span>
                      <span className="text-xs text-muted">
                        {g.seed_domains.join(", ")}
                        {g.elbow_rank
                          ? ` | ~${g.elbow_rank.toLocaleString("en-US")} to the inflection`
                          : " | no inflection found"}
                      </span>
                    </span>
                  </summary>
                  <div className="px-1 pt-1">
                    <ResultRows items={rows} />
                    {rows.length === 0 && (
                      <p className="py-2 text-xs text-muted">
                        This group&apos;s rows sit deeper than what&apos;s loaded; Load more below reaches them.
                      </p>
                    )}
                  </div>
                </details>
              );
            })
          ) : (
            <ResultRows items={visibleItems} />
          )}
          {result.next_cursor && (
            <div className="mt-3 border-t border-hairline pt-3">
              <Button variant="secondary" size="sm" fullWidth loading={loadingMore} onClick={onLoadMore}>
                Load more
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
