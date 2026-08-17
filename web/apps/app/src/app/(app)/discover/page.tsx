"use client";

import { useEffect } from "react";
import { Button, PageFooter, useToast } from "@bower/ui";

import { parseDomains } from "./_components/domains";
import { EXAMPLE_SEEDS, ResultsPanel } from "./_components/results";
import { SearchControls, recordPastSearch } from "./_components/search-controls";
import {
  SearchPhase,
  cancelSearch,
  loadMoreResults,
  resumeActiveRun,
  runSearch,
  useDiscoverSearchStore,
} from "./_components/search-store";

const FORM_ID = "lookalike-search";

/** Discover: seed domains in, a confidence-cut lead list out. The (app)
 * layout owns the auth guard and the shell; this page is only the
 * controls band over the results surface, with a pinned action bar.
 * All search state lives in the module-scope store, so navigating away
 * mid-run and returning shows the run still in progress. */
export default function DiscoverPage() {
  const toast = useToast();
  const seeds = useDiscoverSearchStore((s) => s.seeds);
  const limitRaw = useDiscoverSearchStore((s) => s.limitRaw);
  const excludeRaw = useDiscoverSearchStore((s) => s.excludeRaw);
  const phase = useDiscoverSearchStore((s) => s.phase);
  const result = useDiscoverSearchStore((s) => s.result);
  const error = useDiscoverSearchStore((s) => s.error);
  const loadingMore = useDiscoverSearchStore((s) => s.loadingMore);
  const setSeeds = useDiscoverSearchStore((s) => s.setSeeds);
  const setLimitRaw = useDiscoverSearchStore((s) => s.setLimitRaw);
  const setExcludeRaw = useDiscoverSearchStore((s) => s.setExcludeRaw);
  const consumeError = useDiscoverSearchStore((s) => s.consumeError);

  const searching = phase !== SearchPhase.Idle;
  const excludedSet = new Set(parseDomains(excludeRaw).domains);

  // A reload killed any in-flight poll loop; re-attach to the run the
  // worker kept computing. No-op on plain in-app navigation.
  useEffect(() => {
    resumeActiveRun();
  }, []);

  // Failures may land while the user is on ANOTHER page (the loop outlives
  // this component); they wait in the store and toast on (re)entry.
  useEffect(() => {
    if (error === null) return;
    const message = consumeError();
    if (message) toast.error(message);
  }, [error, consumeError, toast]);

  function submit(raw: string) {
    const { domains, dropped } = parseDomains(raw);
    // Cohorts are required: corroboration between seeds is what makes the
    // ranking good, so the backend rejects single-seed queries too.
    if (domains.length < 2) {
      toast.error("2+ seed domains required.");
      return;
    }
    if (dropped > 0) {
      toast.error(`Too many seeds: searching the first ${domains.length.toLocaleString()}, ${dropped.toLocaleString()} dropped.`);
    }
    recordPastSearch(domains.join(", "));
    runSearch({ domains }, raw);
  }

  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-7xl space-y-6 pb-24 pt-2">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Discover</h1>
          <p className="mt-1 text-sm text-muted">
            Lookalike companies: sites that read like your examples, from an index of 8M+ homepages.
          </p>
        </div>

        {/* Controls band across the top; the results surface below owns
            the whole outcome (count, download, table). */}
        <SearchControls
          formId={FORM_ID}
          seeds={seeds}
          onSeedsChange={setSeeds}
          limitRaw={limitRaw}
          onLimitChange={setLimitRaw}
          excludeRaw={excludeRaw}
          onExcludeChange={setExcludeRaw}
          searching={searching}
          onSubmit={() => submit(seeds)}
          onRunPast={(past) => {
            setSeeds(past);
            submit(past);
          }}
        />

        {/* Persistent live region: it exists before results land, so a
            screen reader announces the results when the Card is inserted
            (a conditionally-mounted live region would not be announced). */}
        <div aria-live="polite" className="min-w-0">
          <ResultsPanel
            searching={searching}
            computing={phase === SearchPhase.Computing}
            result={result}
            excluded={excludedSet}
            limitRaw={limitRaw}
            loadingMore={loadingMore}
            onLoadMore={loadMoreResults}
            onRunExample={() => {
              setSeeds(EXAMPLE_SEEDS);
              submit(EXAMPLE_SEEDS);
            }}
          />
        </div>
      </div>

      {/* The action bar never scrolls away; the submit associates with the
          controls form via the form attr. */}
      <PageFooter>
        {searching && (
          <Button variant="secondary" onClick={cancelSearch}>
            Cancel
          </Button>
        )}
        <Button type="submit" form={FORM_ID} loading={searching}>
          {searching ? "Searching…" : "Find lookalikes"}
        </Button>
      </PageFooter>
    </div>
  );
}
