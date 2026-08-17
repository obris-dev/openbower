"use client";

import { useEffect, useState } from "react";
import { Button, Card, Dropdown, DropdownButton, DropdownItem, DropdownMenu, Input, Label, Textarea } from "@bower/ui";

// Recent searches, kept client-side (an honest local "past searches";
// durable server-side history comes with account-scoped search records).
const PAST_KEY = "bower.discover.past";
const PAST_MAX = 8;

type PastSearch = { seeds: string; ts: number };

function readPastSearches(): PastSearch[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(PAST_KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    // localStorage is writable by anything on the origin: validate the
    // shape entry by entry instead of trusting a cast.
    return parsed
      .filter(
        (p): p is PastSearch =>
          typeof p === "object" && p !== null && typeof (p as PastSearch).seeds === "string" && typeof (p as PastSearch).ts === "number",
      )
      .slice(0, PAST_MAX);
  } catch {
    return [];
  }
}

export function recordPastSearch(seeds: string): void {
  const next = [{ seeds, ts: Date.now() }, ...readPastSearches().filter((p) => p.seeds !== seeds)].slice(0, PAST_MAX);
  try {
    window.localStorage.setItem(PAST_KEY, JSON.stringify(next));
  } catch {
    // Storage unavailable (private mode etc.): history just doesn't persist.
  }
}

/** The search controls, a band across the top of the surface: seed
 * input, results cap, excludes, past searches. Purely presentational
 * over the parent's form state; the parent owns the submit (the
 * page-level footer button posts this form by id). */
export function SearchControls({
  formId,
  seeds,
  onSeedsChange,
  limitRaw,
  onLimitChange,
  excludeRaw,
  onExcludeChange,
  searching,
  onSubmit,
  onRunPast,
}: {
  formId: string;
  seeds: string;
  onSeedsChange: (value: string) => void;
  limitRaw: string;
  onLimitChange: (value: string) => void;
  excludeRaw: string;
  onExcludeChange: (value: string) => void;
  searching: boolean;
  onSubmit: () => void;
  onRunPast: (seeds: string) => void;
}) {
  const [pastSearches, setPastSearches] = useState<PastSearch[]>([]);

  useEffect(() => {
    // Syncing FROM localStorage after mount is the SSR-safe pattern (a
    // lazy initializer would mismatch the server-rendered empty list).
    // Re-keyed on `searching` so a search recorded THIS session appears
    // without a remount (each submit flips the flag).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPastSearches(readPastSearches());
  }, [searching]);

  return (
    <div className="w-full space-y-3">
      {/* The house Dropdown (Headless Menu): keyboard nav, Escape,
          outside-click, and focus return come with the primitive. */}
      <Dropdown>
        <DropdownButton as={Button} variant="ghost" size="sm">
          Past searches
        </DropdownButton>
        <DropdownMenu anchor="bottom start" className="w-72">
          {pastSearches.length === 0 ? (
            <DropdownItem disabled>No searches yet.</DropdownItem>
          ) : (
            pastSearches.map((p) => (
              <DropdownItem key={p.ts} className="truncate" onClick={() => onRunPast(p.seeds)}>
                {p.seeds}
              </DropdownItem>
            ))
          )}
        </DropdownMenu>
      </Dropdown>

      {/* ONE card, one form, hierarchy stated by structure: the seed
          paste is the query and owns the full width; the two optional
          refinements sit as a quiet row beneath a hairline. */}
      <Card className="p-4">
        <form
          id={formId}
          onSubmit={(e) => {
            e.preventDefault();
            if (!searching) onSubmit();
          }}
        >
          <div>
            <Label htmlFor="seeds">Starting company domains</Label>
            <Textarea
              id="seeds"
              rows={3}
              value={seeds}
              onChange={(e) => onSeedsChange(e.target.value)}
              placeholder="acme.com, example.io (2+ required)"
              // Locked during a search so an in-flight response can't
              // land results for a query the box no longer shows.
              disabled={searching}
            />
            <p className="mt-1 text-xs text-muted">
              Paste 2+ domains, comma, space, or newline separated (up to 5,000).
            </p>
          </div>

          <div className="mt-4 grid gap-4 border-t border-hairline pt-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="results-limit">Results limit</Label>
              <Input
                id="results-limit"
                type="number"
                min={1}
                value={limitRaw}
                onChange={(e) => onLimitChange(e.target.value.replace(/[^0-9]/g, ""))}
                placeholder="Auto: up to the inflection point"
              />
            </div>
            <div>
              <Label htmlFor="exclude">Exclude companies</Label>
              <Input
                id="exclude"
                value={excludeRaw}
                onChange={(e) => onExcludeChange(e.target.value)}
                placeholder="domains to leave out"
              />
            </div>
          </div>
        </form>
      </Card>
    </div>
  );
}
