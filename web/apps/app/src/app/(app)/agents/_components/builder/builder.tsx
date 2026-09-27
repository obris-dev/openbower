"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, FieldError, Input, Label, useToast } from "@bower/ui";
import {
  createAgent,
  deleteAgent,
  fetchAgentCatalog,
  fetchAllLists,
  updateAgent,
  webRoutes,
  type AgentCatalog,
  type AgentConfig,
  type AgentOutput,
  type AgentSummary,
  type CellRunResult,
  type AgentTools,
  type ListSummary,
} from "@bower/api";

import { AGENT_LABEL_MAX_LENGTH, TEST_KEY_MAX_LENGTH, TEST_ROW_MAX_KEYS, TEST_VALUE_MAX_LENGTH } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { Breadcrumbs } from "../../../_components/breadcrumbs";
import { configMissing, EMPTY_TOOLS, sheetsTruncatedNote } from "../../../_components/agent-config";
import { type Attempt, buildChecklist, type Draft, draftEquals, draftProvider, EMPTY_OUTPUT, firstGap, goToSection, isContentful, ModelPicker, OutputsEditor, outputsProblem, PromptEditor, promptVariables, type Provider, saveShape, stripVariable, ToolToggles, useAgentDraft } from "../../../_components/agent-config";
import { BuilderFooter } from "./footer";
import { TestPreview } from "./test-preview";
import { usePreviewRun } from "./use-preview-run";

type ModelTriple = { provider: Provider; source: string; model: string };

/** The builder: the prompt
 * card with the test preview beneath it, a configuration rail (Model,
 * Outputs, Tools), actions in the pinned footer. The WHOLE working
 * state (prompt, config, test inputs, last result) drafts to
 * localStorage per agent (custody lives in ./draft), so navigating
 * away loses nothing; Clear draft resets to the saved agent (or
 * blank). Drafts initialize state DURING first render, which is safe
 * only because the wrapper renders this client-only (ssr: false):
 * there is no server HTML to disagree with. The run lifecycle lives
 * in ./use-preview-run, readiness in ./readiness, the template grammar
 * in ./template: this component holds form state and composition. */
export function BuilderForm({ agent }: { agent?: AgentSummary }) {
  const router = useRouter();
  const toast = useToast();
  const { draft: storedDraft, save: saveDraft, clear: removeDraft } = useAgentDraft(agent?.id);
  // A draft written against an OLDER save of this row is stale (the
  // row changed in another session); the server row wins over it,
  // and the discard is SAID, not silent (that is its own data loss).
  const draft = storedDraft && storedDraft.savedAt === agent?.updated_at ? storedDraft : null;
  const draftDiscarded = storedDraft !== null && draft === null;
  const { testBusy, testStale, runTest } = usePreviewRun();

  const [label, setLabel] = useState(draft?.label ?? agent?.label ?? "");
  const [prompt, setPrompt] = useState(draft?.prompt ?? agent?.config.prompt ?? "");
  // ONE state for the model address: the three parts only ever move
  // together (picker selection, seeding, resets), and a single object
  // makes a half-woven triple unrepresentable.
  const [triple, setTriple] = useState<ModelTriple>({
    provider: draft?.provider !== undefined ? draftProvider(draft.provider) : (agent?.config.provider ?? ""),
    source: draft?.source ?? agent?.config.source ?? "",
    model: draft?.model ?? agent?.config.model ?? "",
  });
  const { provider, source, model } = triple;
  const [tools, setTools] = useState<AgentTools>(draft?.tools ?? agent?.config.tools ?? { ...EMPTY_TOOLS });
  // At least one output, always: the editor never lets the count reach
  // zero (the row starts EMPTY with teaching placeholders).
  const [outputs, setOutputs] = useState<AgentOutput[]>(
    draft?.outputs?.length ? draft.outputs : (agent?.config.outputs ?? [{ ...EMPTY_OUTPUT }]),
  );
  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [lists, setLists] = useState<ListSummary[]>([]);
  const [listsLoading, setListsLoading] = useState(true);
  const [listsTruncated, setListsTruncated] = useState(false);
  const [testRow, setTestRow] = useState<Record<string, string>>(draft?.testRow ?? {});
  // Lifted from the prompt editor and preview so Clear draft resets them.
  const [variablesListId, setVariablesListId] = useState("");
  const [borrowListId, setBorrowListId] = useState("");
  const [testResult, setTestResult] = useState<CellRunResult | null>(draft?.testResult ?? null);
  // The tools state THAT PRODUCED the stored result: the preview's
  // no-searches diagnosis must describe the run, not today's toggles.
  const [testToolsOn, setTestToolsOn] = useState(draft?.testToolsOn ?? false);
  // Same custody for the OUTPUTS that produced it: the result's cells
  // render by the run's declared types, not the live editor's.
  const [testOutputs, setTestOutputs] = useState<AgentOutput[]>(
    // Legacy degrade: a draft from before this field carries a result
    // but no snapshot; the live outputs beat rendering it untyped.
    draft?.testOutputs ?? (draft?.testResult ? (draft?.outputs ?? agent?.config.outputs ?? []) : []),
  );
  const [busy, setBusy] = useState(false);
  // Bumped on wholesale form resets (Clear draft): remounts children
  // that keep internal per-row state (the outputs editor's row keys).
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [attempted, setAttempted] = useState<Attempt>(null);

  // Retry is DECLARATIVE: bumping the attempt re-runs the catalog
  // effect (an imperative load fn shared by mount and a click would
  // set state synchronously from the effect body).
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  useEffect(() => {
    async function loadCatalog() {
      const res = await fetchAgentCatalog();
      if (!ensureOk(res, toast, { title: "Catalog unavailable" })) {
        // The picker keeps the SET value visible and offers Retry; a
        // failed catalog must never blank a chosen model.
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
    // page-load snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogAttempt]);

  useEffect(() => {
    async function loadLists() {
      const res = await fetchAllLists();
      if (ensureOk(res, toast, { title: "Sheets unavailable" })) {
        setLists(res.data.items);
        // Surfaced AT the sheet pickers, not as a mount toast: a
        // degradation note belongs beside the control it degrades.
        setListsTruncated(res.data.truncated);
      }
      // Loading ends either way: a failed fetch leaves empty selects
      // with their own copy, never an eternal loading dress.
      setListsLoading(false);
    }
    void loadLists();
    // Mount-only: the sheets are a page-load snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The form's BASELINE: the saved row, or a blank builder with the
  // catalog's default model. The stored draft exists IFF the form
  // differs from this, so merely visiting never writes a masking
  // draft, and Clear draft leaves nothing behind to restore.
  const baseline = useMemo<Draft>(
    () => ({
      savedAt: agent?.updated_at,
      label: agent?.label ?? "",
      prompt: agent?.config.prompt ?? "",
      provider: agent?.config.provider ?? catalog?.models[0]?.provider ?? "",
      source: agent?.config.source ?? catalog?.models[0]?.source ?? "",
      model: agent?.config.model ?? catalog?.models[0]?.model ?? "",
      tools: agent?.config.tools ?? { ...EMPTY_TOOLS },
      outputs: agent?.config.outputs ?? [{ ...EMPTY_OUTPUT }],
      testRow: {},
      testResult: null,
      testOutputs: [],
      testToolsOn: false,
    }),
    [agent, catalog],
  );
  // Memoized so the equality memos below get STABLE deps (a fresh
  // object per render would defeat them).
  const current: Draft = useMemo(
    () => ({
      savedAt: agent?.updated_at,
      label,
      prompt,
      provider,
      source,
      model,
      tools,
      outputs,
      testRow,
      testResult,
      testOutputs,
      testToolsOn,
    }),
     
    [label, prompt, provider, source, model, tools, outputs, testRow, testResult, testOutputs, testToolsOn, agent],
  );
  // Memoized: both compares stringify the payload, and re-running
  // them on every render taxes each keystroke twice.
  const dirty = useMemo(() => !draftEquals(current, baseline), [current, baseline]);
  // ONE projection (draft.ts saveShape) feeds the request config, the
  // Save label, and the Unsaved banner, so they cannot re-drift.
  const shaped = useMemo(() => saveShape(current), [current]);
  const unsaved = useMemo(() => !draftEquals(shaped, saveShape(baseline)), [shaped, baseline]);

  useEffect(() => {
    if (dirty) saveDraft(current);
    else removeDraft();
    // The payload is rebuilt from these state values each render;
    // saveDraft/removeDraft close over a constant key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, label, prompt, provider, source, model, tools, outputs, testRow, testResult, testOutputs, testToolsOn]);

  function clearDraft() {
    removeDraft();
    setLabel(agent?.label ?? "");
    setPrompt(agent?.config.prompt ?? "");
    setTriple({
      provider: agent?.config.provider ?? catalog?.models[0]?.provider ?? "",
      source: agent?.config.source ?? catalog?.models[0]?.source ?? "",
      model: agent?.config.model ?? catalog?.models[0]?.model ?? "",
    });
    setTools(agent?.config.tools ?? { ...EMPTY_TOOLS });
    setOutputs(agent?.config.outputs ?? [{ ...EMPTY_OUTPUT }]);
    setTestRow({});
    setTestResult(null);
    setTestToolsOn(false);
    setTestOutputs([]);
    setVariablesListId("");
    setBorrowListId("");
    setEditorEpoch((epoch) => epoch + 1);
  }

  const config = useMemo<AgentConfig | null>(() => {
    // Every started row must be one the server would accept
    // (readiness mirrors the serializer through outputsProblem): a
    // description-only row blocks with its cause instead of being
    // silently dropped on save.
    if (!shaped.prompt || !provider || !source || !model || !shaped.outputs?.length) return null;
    if (outputsProblem(outputs) !== null) return null;
    return { prompt: shaped.prompt, provider, source, model, tools, outputs: shaped.outputs };
  }, [shaped, provider, source, model, tools, outputs]);

  // Field-level diagnosis over a compound toast: a blocked Test/Save
  // highlights the SECTIONS that are missing, each with its own cause,
  // and every highlight clears itself the moment its field is fixed.
  const outputsIssue = useMemo(() => outputsProblem(outputs), [outputs]);
  const outputsCause = outputsIssue?.message ?? null;
  const missing = useMemo(
    () => ({ label: !label.trim(), ...configMissing({ prompt, provider, source, model, outputs }) }),
    [label, prompt, provider, source, model, outputs],
  );
  const problems = {
    label: attempted === "save" && missing.label,
    prompt: attempted !== null && missing.prompt,
    model: attempted !== null && missing.model,
    outputs: attempted !== null && missing.outputs,
  };
  const checklist = attempted !== null ? buildChecklist(attempted, missing) : null;
  const showChecklist = checklist !== null && checklist.some((item) => item.missing);

  // The preview's inputs are the prompt's ROOT {{variables}} (grammar in
  // ./template), NOTHING FORCED: every tool derives its context from
  // the rendered prompt, so the variables are the whole input surface.
  const inputKeys = useMemo(() => promptVariables(prompt), [prompt]);

  // Removing a test input removes its {{token}} from the prompt (the
  // input exists BECAUSE the prompt references it; same semantics as
  // clicking the chip off) and drops its value.
  function removeTestKey(key: string) {
    setPrompt((current) => stripVariable(current, key));
    setTestRow((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  // The jump runs from an EFFECT: blockOn's render (the warned rings,
  // the data-problem row mark) must commit BEFORE the DOM query, or
  // the first blocked attempt lands on row one.
  const [jump, setJump] = useState<{ anchor: string; at: number } | null>(null);
  useEffect(() => {
    if (jump) goToSection(jump.anchor);
  }, [jump]);

  function blockOn(attempt: Exclude<Attempt, null>): boolean {
    const blocked = attempt === "save" ? !label.trim() || !config : !config;
    if (blocked) {
      setAttempted(attempt);
      const anchor = firstGap(attempt, missing);
      if (anchor) setJump({ anchor, at: Date.now() });
    }
    return blocked;
  }

  async function test() {
    if (blockOn("test") || !config) return;
    setAttempted(null);
    setTestResult(null);
    setTestToolsOn(Object.values(config.tools).some(Boolean));
    setTestOutputs(config.outputs);
    // PROMPT order, filled values only, bounded client-side: the
    // server REFUSES a row past the preview bounds (never truncates),
    // so the client cuts first, in prompt order (edit order would
    // drop a variable the user filled first but typed into last),
    // and the preview diagnoses what the cut leaves out. Keys past the
    // key bound are dropped too: a variable the server would refuse
    // must not make the whole test unrunnable.
    const row = Object.fromEntries(
      inputKeys
        .filter((key) => key.length <= TEST_KEY_MAX_LENGTH)
        // Values clamp here too: typing is bounded by the input's
        // maxLength, but a restored draft is not, and the server
        // refuses rather than truncates.
        .map((key) => [key, (testRow[key] ?? "").slice(0, TEST_VALUE_MAX_LENGTH)] as const)
        .filter(([, value]) => value !== "")
        .slice(0, TEST_ROW_MAX_KEYS),
    );
    const result = await runTest(config, row);
    if (result) setTestResult(result);
  }

  async function save() {
    if (blockOn("save") || !config) return;
    setAttempted(null);
    setBusy(true);
    const res = agent
      ? await updateAgent(agent.id, { label: shaped.label ?? "", config })
      : await createAgent(shaped.label ?? "", config);
    setBusy(false);
    if (!ensureOk(res, toast, { title: "Save failed" })) return;
    removeDraft();
    router.push(webRoutes.agents);
    router.refresh();
  }

  async function remove() {
    if (!agent) return;
    setBusy(true);
    const res = await deleteAgent(agent.id);
    setBusy(false);
    if (!ensureOk(res, toast)) return;
    // A deleted agent's draft would otherwise orphan in localStorage.
    removeDraft();
    router.push(webRoutes.agents);
    router.refresh();
  }

  return (
    <>
      <div className="min-w-0">
        <Breadcrumbs trail={[{ label: "Agents", href: webRoutes.agents }]} />
        <h1 className="mt-1 text-2xl font-bold text-foreground">{agent ? agent.label : "New agent"}</h1>
        {agent && unsaved && (
          // The form is showing MORE than the saved row: say so, with
          // both exits named (a silent draft masking the server row
          // reads as data loss when the other device's edit vanishes).
          <p className="mt-0.5 text-xs text-warning">Unsaved changes | Save keeps them, Clear draft discards them.</p>
        )}
        {draftDiscarded && !unsaved && (
          <p className="mt-0.5 text-xs text-muted">
            A draft from before this agent&rsquo;s latest save was discarded; you are seeing the saved version.
          </p>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <Card className="min-w-0 space-y-4 p-6">
          <div>
            <Label htmlFor="agent-label">Name</Label>
            <Input
              id="agent-label"
              autoFocus={!agent}
              maxLength={AGENT_LABEL_MAX_LENGTH}
              warned={problems.label}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Decision-maker finder"
            />
            {problems.label && <FieldError tone="warning">Name the agent to save it.</FieldError>}
          </div>
          <PromptEditor
            prompt={prompt}
            onChange={setPrompt}
            warned={problems.prompt}
            source={{
              kind: "picker",
              lists,
              listsLoading,
              listsTruncated,
              sourceListId: variablesListId,
              onSourceList: setVariablesListId,
            }}
          />
          <TestPreview
            inputKeys={inputKeys}
            lists={lists}
            listsLoading={listsLoading}
            listsTruncated={listsTruncated}
            testRow={testRow}
            onTestRow={setTestRow}
            result={testResult}
            busy={testBusy}
            stale={testStale}
            sourceListId={borrowListId}
            onSourceList={setBorrowListId}
            onRemoveKey={removeTestKey}
            toolsOn={testToolsOn}
            outputs={testOutputs}
            supportFollowup={catalog?.support_followup}
          />
        </Card>

        <div className="min-w-0 space-y-4">
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
            key={editorEpoch}
            outputs={outputs}
            onChange={setOutputs}
            warned={problems.outputs}
            cause={problems.outputs ? outputsCause : null}
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
      </div>

      <BuilderFooter
        checklist={showChecklist ? checklist : null}
        showDelete={Boolean(agent)}
        busy={busy}
        testBusy={testBusy}
        onDelete={() => void remove()}
        onClearDraft={clearDraft}
        onTest={() => void test()}
        onSave={() => void save()}
      />
    </>
  );
}
