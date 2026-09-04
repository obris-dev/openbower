"use client";

import { useMemo } from "react";
import { Button, Card, Combobox, type ComboboxItem } from "@bower/ui";
import type { AgentCatalog } from "@bower/api";

import { modelsTruncatedNote } from "./copy";

// The option value is an opaque JSON triple: source names and model
// names are user/env-authored, so no separator character is safe.
function pack(provider: string, source: string, model: string): string {
  return JSON.stringify([provider, source, model]);
}

/** The model picker: a searchable combobox grouped by SOURCE alone
 * (the API-spec door behind a source is plumbing, never a choice).
 * Options carry {provider, source,
 * model}; selection sets all three. A stored model the catalog no
 * longer offers stays visible, flagged: runs degrade to empty cells,
 * and silently selecting something else would be worse. */
export function ModelPicker({
  catalog,
  failed,
  onRetry,
  warned,
  provider,
  source,
  model,
  onChange,
}: {
  catalog: AgentCatalog | null;
  failed: boolean;
  onRetry: () => void;
  warned: boolean;
  provider: string;
  source: string;
  model: string;
  onChange: (provider: string, source: string, model: string) => void;
}) {
  const currentValue = pack(provider, source, model);
  const loading = catalog === null && !failed;
  // Memoized: a fresh array per keystroke would defeat the combobox's
  // own grouping memo (it keys on items identity).
  const { items, vanished } = useMemo(() => {
    // Grouped by SOURCE alone: users pick "ollama" or "openai", and
    // the API-spec door behind a source is plumbing, not a choice.
    const built: ComboboxItem[] = (catalog?.models ?? []).map((m) => ({
      value: pack(m.provider, m.source, m.model),
      label: m.model,
      group: m.source,
    }));
    const gone = Boolean(model) && catalog !== null && !built.some((i) => i.value === currentValue);
    if (gone) {
      built.unshift({ value: currentValue, label: `${model} (unavailable)`, group: "Saved on this agent" });
    }
    if (catalog === null && model) {
      // The LOADING dress of the same control: the saved model stays
      // visible in the (disabled) input; the full list replaces this
      // single entry in place when the catalog lands.
      built.push({ value: currentValue, label: model, group: source });
    }
    return { items: built, vanished: gone };
  }, [catalog, currentValue, model, source]);

  return (
    <Card id="agent-model" className={warned ? "space-y-3 p-4 ring-1 ring-inset ring-warning-edge" : "space-y-3 p-4"}>
      <p className="text-xs font-semibold uppercase tracking-wide text-faint">Model</p>
      {warned && <p className="text-xs text-warning">Pick a model to run this agent on.</p>}
      {catalog !== null && catalog.models.length === 0 && items.length === 0 ? (
        <>
          <p className="text-sm text-muted">No models are available on this deployment; {catalog.support_followup}.</p>
          {/* The setup walkthrough, under the copy rule's carve-out:
              framed so hosted users can tell it is not for them. */}
          <p className="text-xs text-faint">
            Self-hosting? config/providers.toml declares your sources; setup seeds it with a local Ollama entry, and
            any OpenAI-compatible or Anthropic-compatible server is one entry more. If someone else runs this
            deployment, send them this note.
          </p>
        </>
      ) : (
        // ONE control across loading/failed/loaded (a text-line
        // stand-in that swaps to a mounted combobox reads as the list
        // popping in piecemeal): while the catalog resolves, the same
        // combobox renders DISABLED with the saved model in place and
        // "Loading models…" as its empty placeholder, then enables
        // with the complete list, no layout shift.
        <Combobox
          items={items}
          warned={warned}
          disabled={catalog === null}
          aria-busy={loading || undefined}
          value={currentValue}
          onChange={(next) => {
            if (!next) {
              // The combobox's CLEAR gesture: empty the whole address
              // (readiness flags it; snapping back would make the
              // field uncleareable).
              onChange("", "", "");
              return;
            }
            try {
              const [p, s, m] = JSON.parse(next) as [string, string, string];
              if (p && s && m) onChange(p, s, m);
            } catch {
              // An unparseable value never comes from our own options.
            }
          }}
          placeholder={loading ? "Loading models…" : "Search models"}
          aria-label="Model"
        />
      )}
      {/* Only when a saved model occupies the input (the empty input's
          placeholder already says it): two identical lines stacked. */}
      {loading && Boolean(model) && <p className="text-xs text-faint">Loading models…</p>}
      {catalog === null && failed && (
        <>
          <p className="text-xs text-danger">The model catalog could not be loaded.</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </>
      )}
      {catalog?.truncated && (
        <p className="text-xs text-faint">{modelsTruncatedNote(catalog.models.length)}</p>
      )}
      {vanished &&
        (catalog?.truncated ? (
          // A truncated catalog cannot distinguish absent from
          // beyond-the-cap: the model may still run (the server
          // validates against the FULL roster).
          <p className="text-xs text-muted">
            This model isn&rsquo;t among the models shown (the catalog is truncated); it may still run, and a test
            will tell.
          </p>
        ) : (
          <p className="text-xs text-warning">
            This model isn&rsquo;t available on this server anymore (removed from its source, or its source is
            gone); pick another (tests refuse to start on an unavailable model).
          </p>
        ))}
    </Card>
  );
}
