"use client";

import { useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Button, Drawer, ErrorMessage, FieldError, Input, useToast } from "@bower/ui";
import {
  fetchAgent,
  fetchAgentCatalog,
  fetchAgents,
  type AgentCatalog,
  type AgentConfig,
  type AgentListItem,
  type AgentOutput,
  type AgentSummary,
  type AgentTools,
  type ListColumn,
  COLUMN_COLLISION_CODE,
  DERIVED_KEY_COLLISION_CODE,
  RESERVED_KEY_CODE,
} from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { DEFAULT_SCOPE_ROWS, defaultScopeKind, effectiveRows, parseScopeRows, type ScopeChoice } from "../fill";
import { type Attempt, buildChecklist, configMissing, configReady, draftProvider, EMPTY_OUTPUT, EMPTY_TOOLS, firstGap, goToSection, isContentful, type Missing, ModelPicker, OutputsEditor, outputsProblem, PromptEditor, type Provider, ReadinessChecklist, ToolToggles } from "../../../../_components/agent-config";
import { AgentsTab } from "./agents-tab";
import { collidingKeys } from "./landing-keys";

export type AiColumnPayload = {
  config?: AgentConfig;
  agent_id?: string;
  confirmed_row_count: number;
  /** First-N row scope; omitted means every row. */
  rows?: number;
};

/** A submission's outcome, declared HERE because this drawer renders
 * the refusal (field-level where it can): `error` is the machine
 * code, `detail` the server's verbatim copy, both required so a
 * missing-copy case is unrepresentable (a LEAVING outcome carries ""
 * for both, which renders nothing while login navigation lands). */
export type ColumnOutcome = { ok: true } | { ok: false; error: string; detail: string };

type Tab = "prompt" | "agents";
type ModelTriple = { provider: Provider; source: string; model: string };

const FORM_ID = "use-ai-form";

const TABS: { key: Tab; label: string }[] = [
  { key: "prompt", label: "New prompt" },
  { key: "agents", label: "Your agents" },
];

// Refusal codes whose offending surface is the OUTPUTS (they name
// what the fill would write): the server's verbatim detail renders in
// the AI pane beside the collision pre-warnings; every other code
// keeps the footer slot.
const SCOPE_INPUT_ID = "fill-scope-rows";
const SCOPE_ERROR_ID = "fill-scope-rows-error";

const OUTPUTS_REFUSAL_CODES = new Set<string>([
  COLUMN_COLLISION_CODE,
  RESERVED_KEY_CODE,
  DERIVED_KEY_COLLISION_CODE,
]);

// Roving tabindex + arrow keys: role=tab announces the APG keyboard
// contract, so the widget must honor it (selection follows focus; the
// unselected tab leaves the Tab sequence via tabIndex=-1).
function tablistNav<K extends string>(order: readonly K[], current: K, select: (next: K) => void, idFor: (key: K) => string) {
  return (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = order[(order.indexOf(current) + step + order.length) % order.length];
    if (next === undefined) return;
    select(next);
    document.getElementById(idFor(next))?.focus();
  };
}

/** The Use AI drawer over the sheet (the Add column menu's AI kind;
 * a plain column is named in the grid and a webhook has its own
 * drawer). Two ways to a runnable fill ("New prompt" composes the
 * shared config editor; "Your agents" points a saved one), a
 * row-scope choice (First N | All rows) whose effective count drives
 * the counts-only consent footer and the labeled submit; its OUTPUTS
 * are the columns (each output's key and label name what its cells
 * land under, so no column-name field renders), and its prompt
 * variables are THIS sheet's columns. Presentational: the parent owns
 * `open`, the POST behind onSubmit, and every after-effect of a start
 * (closing, refreshing counts). */
export function UseAiDrawer(props: {
  open: boolean;
  onClose: () => void;
  rowCount: number;
  columns: ListColumn[];
  onSubmit: (payload: AiColumnPayload) => Promise<ColumnOutcome>;
}) {
  // Mounted fresh per open: state resets with the gesture, autoFocus
  // lands on a fresh mount, and no fetch runs while the drawer is
  // closed. The unmount trails the panel's leave motion, so the
  // content stays put while the panel slides out.
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      open={props.open}
      // A reopen that lands mid-leave cancels the close, so the panel
      // is already sliding back in and its content must not unmount
      // under it.
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
      rowCount={props.rowCount}
      columns={props.columns}
      onSubmit={props.onSubmit}
    />
  );
}

function DrawerContent({
  open,
  afterLeave,
  onClose,
  rowCount,
  columns,
  onSubmit,
}: {
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
  rowCount: number;
  columns: ListColumn[];
  onSubmit: (payload: AiColumnPayload) => Promise<ColumnOutcome>;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("prompt");

  // The prompt tab's form state, the config editor's shapes: one object
  // for the model address (its three parts only ever move together).
  const [prompt, setPrompt] = useState("");
  const [triple, setTriple] = useState<ModelTriple>({ provider: "", source: "", model: "" });
  const { provider, source, model } = triple;
  // Web search starts ON for a fresh AI column (a research column
  // defaults to researching; evidence is what the provenance layer
  // verifies against, and a knowledge-only column is one click away).
  const [tools, setTools] = useState<AgentTools>({ ...EMPTY_TOOLS, web_search: true });
  const [outputs, setOutputs] = useState<AgentOutput[]>([{ ...EMPTY_OUTPUT }]);

  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);

  const [agents, setAgents] = useState<AgentListItem[] | null>(null);
  const [agentsFailed, setAgentsFailed] = useState(false);
  const [agentId, setAgentId] = useState("");
  // Details cache by id: a slow fetch landing after the selection
  // moved on writes its own key, never the current selection's.
  const [agentDetails, setAgentDetails] = useState<Record<string, AgentSummary>>({});

  // The fill's row scope: the KIND and the authored N are separate
  // state (switching to All must not erase a typed number), rejoined
  // into one ScopeChoice below. Default First 32 only when the sheet
  // outgrows the default; typing in the N input states first-N intent.
  const [scopeKind, setScopeKind] = useState<"first" | "all">(() => defaultScopeKind(rowCount));
  const [scopeRowsText, setScopeRowsText] = useState(String(DEFAULT_SCOPE_ROWS));

  const [submitting, setSubmitting] = useState(false);
  // Diagnosis waits for a first attempt: a half-typed output row is
  // not yet a problem, and marks that appear while typing read as
  // nagging rather than as help.
  const [attempted, setAttempted] = useState<Attempt>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  // The outputs' own refusal (column_collision, reserved_key,
  // derived_key_collision): renders beside the pre-warnings and
  // self-clears when the outputs or the chosen agent change.
  const [outputsRefusal, setOutputsRefusal] = useState<string | null>(null);

  // Retry is DECLARATIVE (the builder's idiom): bumping the attempt
  // re-runs the effect instead of a shared imperative load fn setting
  // state synchronously from the effect body.
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  useEffect(() => {
    async function loadCatalog() {
      const res = await fetchAgentCatalog();
      if (!ensureOk(res, toast, { title: "Catalog unavailable" })) {
        setCatalogFailed(true);
        return;
      }
      setCatalog(res.data);
      const first = res.data.models[0];
      // Seed the triple ATOMICALLY into a fully empty one (functional
      // read: no stale closure, no half-woven address).
      if (first) {
        setTriple((cur) =>
          cur.provider || cur.source || cur.model
            ? cur
            : { provider: first.provider, source: first.source, model: first.model },
        );
      }
    }
    void loadCatalog();
    // Re-runs only on explicit retry; the catalog is otherwise a
    // per-open snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogAttempt]);

  const [agentsAttempt, setAgentsAttempt] = useState(0);
  useEffect(() => {
    async function loadAgents() {
      const res = await fetchAgents();
      if (!ensureOk(res, toast, { title: "Agents unavailable" })) {
        setAgentsFailed(true);
        return;
      }
      setAgents(res.data.items);
    }
    void loadAgents();
    // Re-runs only on explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentsAttempt]);

  // The selected agent's full config: the list rows carry only what
  // they draw, and the footer's source fact plus the multi-output
  // pre-warnings need the config itself.
  useEffect(() => {
    if (!agentId || agentDetails[agentId]) return;
    const id = agentId;
    async function loadDetail() {
      const res = await fetchAgent(id);
      if (!ensureOk(res, toast, { title: "Agent unavailable" })) return;
      setAgentDetails((cur) => ({ ...cur, [res.data.id]: res.data }));
    }
    void loadDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  // Every submitted config must be one the server would accept
  // (outputsProblem mirrors the serializer); an invalid one keeps the
  // submit disabled rather than 400ing.
  const outputsIssue = useMemo(() => outputsProblem(outputs), [outputs]);
  const config = useMemo<AgentConfig | null>(() => {
    const draft = {
      prompt: prompt.trim(),
      provider,
      source,
      model,
      outputs: outputs.filter(isContentful),
    };
    // The guard narrows the DRAFT, which is what lets this return the
    // contract shape without re-testing that a provider was chosen.
    if (!configReady(draft)) return null;
    return { ...draft, tools };
  }, [prompt, provider, source, model, tools, outputs]);

  const selectedAgent = useMemo(
    () => (agentId ? ((agents ?? []).find((agent) => agent.id === agentId) ?? null) : null),
    [agentId, agents],
  );
  const selectedDetail = agentId ? (agentDetails[agentId] ?? null) : null;

  // PRE-warnings only, and only for a fill: the outputs' own keys
  // matched against the current columns. The client cannot see
  // sheet-wide emptiness from its paged rows, so it never claims
  // occupied or empty; the server decides at admission. The plain
  // kinds carry no warning (their one refusal, a duplicate key,
  // arrives verbatim from the server).
  const collisions = useMemo(() => {
    const forOutputs = tab === "prompt" ? outputs : (selectedDetail?.config.outputs ?? []);
    return collidingKeys(forOutputs, columns);
  }, [tab, outputs, selectedDetail, columns]);

  // A refusal about the outputs describes a submit that is now in
  // the past: any move of its inputs (outputs, tab, chosen agent)
  // clears it at the gesture.
  function onOutputs(next: AgentOutput[]) {
    setOutputs(next);
    setOutputsRefusal(null);
  }
  function onTab(next: Tab) {
    setTab(next);
    setOutputsRefusal(null);
    // An attempt belongs to the tab it was made on. Carried across, a
    // prompt-tab gap showed the agents tab a refusal nobody asked for,
    // and an agents-tab attempt put a warning ring on an output row
    // with no cause beside it, because `missing` is all-false there.
    setAttempted(null);
  }
  function onAgent(id: string) {
    setAgentId(id);
    setOutputsRefusal(null);
  }

  // The effective scope: min(N, rowCount) client-side for display and
  // consent; the server admits the true eligible count and the
  // response is truth. An unparseable N (the input can be emptied)
  // nulls the scope, which keeps the submit disabled.
  const scope = useMemo<ScopeChoice | null>(() => {
    if (scopeKind === "all") return { kind: "all" };
    const n = parseScopeRows(scopeRowsText);
    return n === null ? null : { kind: "first", n };
  }, [scopeKind, scopeRowsText]);
  const scopedRows = scope === null ? null : effectiveRows(scope, rowCount);

  const payload = useMemo<AiColumnPayload | null>(() => {
    if (scope === null || scopedRows === null) return null;
    const rows = scope.kind === "first" ? { rows: scopedRows } : {};
    if (tab === "prompt") {
      return config ? { config, confirmed_row_count: rowCount, ...rows } : null;
    }
    return agentId ? { agent_id: agentId, confirmed_row_count: rowCount, ...rows } : null;
  }, [tab, config, agentId, rowCount, scope, scopedRows]);

  // Which sections a Start fill still needs. The agents tab asks for
  // none of them: its one requirement is a chosen agent, which is not
  // a section of this form and is diagnosed at the picker instead.
  const missing = useMemo<Missing>(
    () =>
      tab === "prompt"
        ? { label: false, ...configMissing({ prompt, provider, source, model, outputs }) }
        : { label: false, prompt: false, model: false, outputs: false },
    [tab, prompt, provider, source, model, outputs],
  );
  // The row scope is a FOOTER control, not one of the config
  // sections, so its gap is diagnosed at the field (tier a) rather
  // than through the checklist. It blocks the submit exactly as the
  // sections do: payload is null while it cannot be parsed.
  const scopeMissing = scopeKind === "first" && scope === null;
  const problems = {
    // `scope` reads like its siblings on purpose: nothing shows before
    // a first attempt, and an unfilled field is WARNED (amber, no
    // aria-invalid), not invalid: danger says "wrong", and an
    // unfinished field is incomplete.
    scope: attempted !== null && scopeMissing,
    prompt: attempted !== null && missing.prompt,
    model: attempted !== null && missing.model,
    outputs: attempted !== null && missing.outputs,
  };
  const checklist = attempted !== null && tab === "prompt" ? buildChecklist("fill", missing) : null;
  const showChecklist = checklist !== null && checklist.some((item) => item.missing);

  // The jump runs from an EFFECT: blockOn's render (the warned rings,
  // the data-problem row mark) must commit BEFORE the DOM query, or
  // the first blocked attempt lands on row one.
  const [jump, setJump] = useState<{ anchor: string; at: number } | null>(null);
  useEffect(() => {
    if (jump) goToSection(jump.anchor);
  }, [jump]);

  // The action stays ENABLED and diagnoses here. A disabled button
  // cannot run this, so nothing would ever set `attempted` and every
  // per-field mark below was unreachable: two outputs deriving one
  // key showed a dead button and no reason.
  function blockOn(): boolean {
    const blocked = !payload;
    if (blocked) {
      setAttempted("fill");
      // Sections first: they are the ask itself. The scope is the last
      // gap because it is the cheapest to see, sitting at the action.
      const anchor = firstGap("fill", missing) ?? (scopeMissing ? SCOPE_INPUT_ID : undefined);
      if (anchor) setJump({ anchor, at: Date.now() });
    }
    return blocked;
  }

  async function submit() {
    if (blockOn() || !payload || submitting) return;
    setAttempted(null);
    setSubmitting(true);
    setServerError(null);
    setOutputsRefusal(null);
    const res = await onSubmit(payload);
    setSubmitting(false);
    if (!res.ok) {
      // Tier 1: the server's detail renders VERBATIM. A code whose
      // offending surface is knowable (the outputs) marks it; the
      // rest keep the footer slot (row_count_changed included: the
      // parent refreshes its count on that code, so the next attempt
      // echoes the new truth). Without a detail the client stays
      // general: it cannot see why the start failed.
      if (res.error && OUTPUTS_REFUSAL_CODES.has(res.error) && res.detail) {
        setOutputsRefusal(res.detail);
      } else {
        setServerError(res.detail);
      }
    }
  }

  const footer = (
      <>
        {serverError && <ErrorMessage message={serverError} />}
        {/* The row scope, chosen BEFORE the spend: the counts
            below and the submit label both follow the effective
            scope, so what the button names is what the footer
            priced. */}
        <fieldset>
          <legend className="sr-only">Rows to fill</legend>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-foreground">
            <div className="flex items-center gap-1.5">
              <input
                id="fill-scope-first"
                type="radio"
                name="fill-scope"
                checked={scopeKind === "first"}
                onChange={() => setScopeKind("first")}
              />
              <label htmlFor="fill-scope-first">First</label>
              <Input
                id={SCOPE_INPUT_ID}
                type="number"
                min={1}
                max={rowCount}
                step={1}
                value={scopeRowsText}
                onChange={(event) => {
                  setScopeRowsText(event.target.value);
                  setScopeKind("first");
                }}
                warned={problems.scope}
                aria-describedby={problems.scope ? SCOPE_ERROR_ID : undefined}
                aria-label="How many rows to fill"
                className="w-20 py-1"
              />
              <span>rows</span>
            </div>
            <div className="flex items-center gap-1.5">
              <input
                id="fill-scope-all"
                type="radio"
                name="fill-scope"
                checked={scopeKind === "all"}
                onChange={() => setScopeKind("all")}
              />
              <label htmlFor="fill-scope-all">All rows</label>
            </div>
          </div>
          {problems.scope && (
            <FieldError id={SCOPE_ERROR_ID} tone="warning">
              Enter how many rows to fill, or choose All rows.
            </FieldError>
          )}
        </fieldset>
        {showChecklist && checklist && <ReadinessChecklist items={checklist} />}
        {attempted !== null && tab === "agents" && !agentId && (
          <FieldError tone="warning">Choose an agent to fill this column, or write a prompt instead.</FieldError>
        )}
        {/* ENABLED even when incomplete: submit() diagnoses. The
            primitive disables itself while loading. */}
        <Button type="submit" form={FORM_ID} fullWidth loading={submitting}>
          {/* No count while the scope is unresolved: falling back to
              the sheet total named a number this click would not run. */}
          {scopedRows === null ? "Start fill" : `Start fill | ${scopedRows.toLocaleString("en-US")} rows`}
        </Button>
      </>
    );

  return (
    <Drawer
      open={open}
      afterLeave={afterLeave}
      onClose={onClose}
      title="Use AI"
      footer={footer}
    >
      <form
        id={FORM_ID}
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="space-y-4"
      >
        <div className="space-y-4">
          <div
            role="tablist"
            aria-label="Fill with"
            onKeyDown={tablistNav(TABS.map(({ key }) => key), tab, onTab, (key) => `tab-${key}`)}
            className="flex gap-1 rounded-lg bg-wash p-1"
          >
            {TABS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`tab-${key}`}
                tabIndex={tab === key ? 0 : -1}
                aria-selected={tab === key}
                aria-controls={`panel-${key}`}
                onClick={() => onTab(key)}
                className={
                  tab === key
                    ? "flex-1 rounded-md bg-surface px-3 py-1.5 text-sm font-medium text-foreground shadow-sm"
                    : "flex-1 rounded-md px-3 py-1.5 text-sm font-medium text-muted hover:text-foreground"
                }
              >
                {label}
              </button>
            ))}
          </div>

          {/* Both tab panels stay mounted too, for the same reason. */}
          <div role="tabpanel" id="panel-prompt" aria-labelledby="tab-prompt" hidden={tab !== "prompt"} className="space-y-4">
            {/* Variables come from THE SHEET THE DRAWER IS ON, no
                selector: the fill runs over these rows and no
                others. */}
            <PromptEditor
              prompt={prompt}
              onChange={setPrompt}
              warned={problems.prompt}
              autoFocus
              source={{ kind: "sheet", columns }}
            />
            <ModelPicker
              catalog={catalog}
              failed={catalogFailed}
              onRetry={() => {
                setCatalogFailed(false);
                setCatalogAttempt((n) => n + 1);
              }}
              warned={problems.model}
              provider={provider}
              source={source}
              model={model}
              onChange={(p, s, m) => setTriple({ provider: draftProvider(p), source: s, model: m })}
            />
            <OutputsEditor
              outputs={outputs}
              onChange={onOutputs}
              warned={problems.outputs}
              cause={problems.outputs ? (outputsIssue?.message ?? null) : null}
              problemIndex={problems.outputs ? (outputsIssue?.index ?? null) : null}
            />
            <ToolToggles
              catalog={catalog}
              failed={catalogFailed}
              onRetry={() => {
                setCatalogFailed(false);
                setCatalogAttempt((n) => n + 1);
              }}
              tools={tools}
              onChange={setTools}
            />
          </div>
          <div role="tabpanel" id="panel-agents" aria-labelledby="tab-agents" hidden={tab !== "agents"}>
            <AgentsTab
              agents={agents}
              failed={agentsFailed}
              onRetry={() => {
                setAgentsFailed(false);
                setAgentsAttempt((n) => n + 1);
              }}
              selectedId={agentId}
              onSelect={onAgent}
              onWritePrompt={() => onTab("prompt")}
            />
          </div>

          {/* The outputs' landing keys, diagnosed in one place for
              both tabs (the agents tab has no outputs editor to
              mark): the server's verbatim refusal, then the
              warning-only pre-checks. */}
          {(outputsRefusal !== null || collisions.length > 0) && (
            <div className="space-y-1">
              {outputsRefusal !== null && <FieldError>{outputsRefusal}</FieldError>}
              {collisions.map((key) => (
                <FieldError key={key} tone="warning">
                  May collide with the existing {key} column; the server checks when you start.
                </FieldError>
              ))}
            </div>
          )}
        </div>
      </form>
    </Drawer>
  );
}
