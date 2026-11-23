"use client";

import { useEffect, useState } from "react";
import { Button, Card, Drawer, ErrorMessage, FieldError, Skeleton, useToast } from "@bower/ui";
import {
  AGENT_OUTPUTS_IN_USE_CODE,
  type AgentCatalog,
  type AgentConfig,
  type AiColumn,
  type ColumnAgentWire,
  fetchAgentCatalog,
  MODEL_UNRUNNABLE_CODE,
  type SheetColumn,
  webRoutes,
} from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { AGENT_TOOLS, draftProvider, ModelPicker, OutputsEditor, PromptEditor, ToolToggles } from "../../../../_components/agent-config";
import {
  CONFIG_FAILED_LINE,
  EDIT_LEDE,
  EDIT_PROMPT_VERB,
  editAgentAction,
  RETRY,
  SAVE_ACTION,
  SAVED_AGENT_LEDE,
} from "./copy";
import { configFrom, draftFrom, type EditDraft, isDirty } from "./edit-draft";
import { useColumnAgent } from "./use-column-agent";
import type { ColumnOutcome } from "./use-ai-drawer";

const FORM_ID = "edit-ai-form";

type Props = {
  open: boolean;
  onClose: () => void;
  sheetId: string;
  /** The column being edited; null only while the drawer is closed. */
  column: AiColumn | null;
  columns: SheetColumn[];
  onSave: (key: string, config: AgentConfig) => Promise<ColumnOutcome>;
};

/** An AI column's agent, opened from the column menu. The column's own
 * agent edits here (prompt, model, tools, output descriptions; the
 * outputs themselves are the sheet's columns). A saved agent may fill
 * other sheets, so it shows read-only with a link to its builder.
 * Mounted fresh per open, so state resets with the gesture. */
export function EditAiDrawer(props: Props) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted || props.column === null) return null;
  return <DrawerContent {...props} column={props.column} afterLeave={() => setMounted(props.open)} />;
}

function DrawerContent({
  open,
  onClose,
  afterLeave,
  sheetId,
  column: columnProp,
  columns,
  onSave,
}: Props & { column: AiColumn; afterLeave: () => void }) {
  const toast = useToast();
  // A mount-time snapshot: the sheet nulls its drawer state on close
  // while the panel is still sliding out.
  const [column] = useState(columnProp);
  const { load, retry } = useColumnAgent(sheetId, column.key);
  const agent = load.status === "ready" ? load.agent : null;
  const editable = agent !== null && !agent.saved;

  // The stored config and the draft seed from the read the moment it
  // arrives (state set during render, the webhook drawer's latch).
  const [seededFrom, setSeededFrom] = useState<ColumnAgentWire | null>(null);
  const [stored, setStored] = useState<AgentConfig | null>(null);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  if (agent !== null && seededFrom !== agent) {
    setSeededFrom(agent);
    setStored(agent.config);
    setDraft(draftFrom(agent.config));
  }

  const [catalog, setCatalog] = useState<AgentCatalog | null>(null);
  const [catalogFailed, setCatalogFailed] = useState(false);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [outputsRefusal, setOutputsRefusal] = useState<string | null>(null);
  const [modelRefusal, setModelRefusal] = useState<string | null>(null);
  const [footerError, setFooterError] = useState<string | null>(null);

  useEffect(() => {
    async function loadCatalog() {
      const res = await fetchAgentCatalog();
      if (!ensureOk(res, toast, { title: "Catalog unavailable" })) {
        setCatalogFailed(true);
        return;
      }
      setCatalog(res.data);
    }
    void loadCatalog();
    // Re-runs only on explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogAttempt]);

  // A row's own answers are not inputs to the prompt that writes them.
  const ownKeys = new Set((stored?.outputs ?? []).map((output) => output.key));
  const variableColumns = columns.filter((candidate) => !ownKeys.has(candidate.key));
  const config = draft === null ? null : configFrom(draft);
  const dirty = draft !== null && stored !== null && isDirty(draft, stored);

  function update(patch: Partial<EditDraft>) {
    setDraft((cur) => (cur === null ? cur : { ...cur, ...patch }));
    setFooterError(null);
  }

  async function save() {
    if (config === null || !dirty || saving) return;
    setSaving(true);
    setOutputsRefusal(null);
    setModelRefusal(null);
    setFooterError(null);
    const outcome = await onSave(column.key, config);
    setSaving(false);
    if (outcome.ok) {
      setStored(config);
      return;
    }
    if (!outcome.detail) return;
    if (outcome.error === AGENT_OUTPUTS_IN_USE_CODE) setOutputsRefusal(outcome.detail);
    else if (outcome.error === MODEL_UNRUNNABLE_CODE) setModelRefusal(outcome.detail);
    else setFooterError(outcome.detail);
  }

  function retryCatalog() {
    setCatalogFailed(false);
    setCatalogAttempt((n) => n + 1);
  }

  const footer = editable ? (
    <>
      {footerError && <ErrorMessage message={footerError} />}
      <Button type="submit" form={FORM_ID} fullWidth loading={saving} disabled={!dirty || config === null}>
        {SAVE_ACTION}
      </Button>
    </>
  ) : undefined;

  let body;
  if (load.status === "failed") {
    body = (
      <div className="space-y-3">
        <ErrorMessage message={CONFIG_FAILED_LINE} />
        <Button type="button" variant="secondary" size="sm" onClick={retry}>
          {RETRY}
        </Button>
      </div>
    );
  } else if (agent === null || draft === null) {
    body = <EditorSkeleton />;
  } else if (agent.saved) {
    body = <SavedAgent agent={agent} />;
  } else {
    body = (
      <form
        id={FORM_ID}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="space-y-4"
      >
        <p className="text-sm text-muted">{EDIT_LEDE}</p>
        <PromptEditor
          prompt={draft.prompt}
          onChange={(prompt) => update({ prompt })}
          warned={draft.prompt.trim() === ""}
          autoFocus
          source={{ kind: "sheet", columns: variableColumns }}
        />
        <div className="space-y-1">
          <ModelPicker
            catalog={catalog}
            failed={catalogFailed}
            onRetry={retryCatalog}
            warned={modelRefusal !== null}
            provider={draft.provider}
            source={draft.source}
            model={draft.model}
            onChange={(provider, source, model) => {
              update({ provider: draftProvider(provider), source, model });
              setModelRefusal(null);
            }}
          />
          {modelRefusal && <FieldError>{modelRefusal}</FieldError>}
        </div>
        <div className="space-y-1">
          <OutputsEditor
            outputs={draft.outputs}
            onChange={(outputs) => {
              update({ outputs });
              setOutputsRefusal(null);
            }}
            warned={false}
            cause={null}
            locked
          />
          {outputsRefusal && <FieldError>{outputsRefusal}</FieldError>}
        </div>
        <ToolToggles
          catalog={catalog}
          failed={catalogFailed}
          onRetry={retryCatalog}
          tools={draft.tools}
          onChange={(tools) => update({ tools })}
        />
      </form>
    );
  }

  return (
    <Drawer open={open} onClose={onClose} title={EDIT_PROMPT_VERB} afterLeave={afterLeave} footer={footer}>
      {body}
    </Drawer>
  );
}

/** The editor's shape, held while the agent is on the way. */
function EditorSkeleton() {
  return (
    <div className="space-y-6" aria-hidden>
      <Skeleton className="h-4 w-3/4 motion-reduce:animate-none" />
      <Skeleton className="h-40 w-full motion-reduce:animate-none" />
      <Skeleton className="h-9 w-full motion-reduce:animate-none" />
      <Skeleton className="h-24 w-full motion-reduce:animate-none" />
    </div>
  );
}

/** A saved agent, read-only: what fills the column, and where to change it. */
function SavedAgent({ agent }: { agent: ColumnAgentWire }) {
  const { config } = agent;
  const tools = AGENT_TOOLS.filter((tool) => config.tools[tool.key]).map((tool) => tool.label);
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted">{SAVED_AGENT_LEDE}</p>
      <Button href={webRoutes.agent(agent.agent_id)} variant="secondary">
        {editAgentAction(agent.agent_label)}
      </Button>
      <dl className="space-y-4 text-sm">
        <div>
          <dt className="text-xs font-semibold text-faint">Prompt</dt>
          <dd className="mt-1 whitespace-pre-wrap break-words text-foreground">{config.prompt}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold text-faint">Model</dt>
          <dd className="mt-1 text-foreground">
            {config.model} | {config.source}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold text-faint">Tools</dt>
          <dd className="mt-1 text-foreground">{tools.length > 0 ? tools.join(" | ") : "None"}</dd>
        </div>
        <div>
          <dt className="text-xs font-semibold text-faint">Outputs</dt>
          <dd className="mt-1 space-y-2">
            {config.outputs.map((output) => (
              <Card key={output.key} className="p-3">
                <p className="font-medium text-foreground">{output.label}</p>
                {output.description && <p className="mt-0.5 text-muted">{output.description}</p>}
              </Card>
            ))}
          </dd>
        </div>
      </dl>
    </div>
  );
}
