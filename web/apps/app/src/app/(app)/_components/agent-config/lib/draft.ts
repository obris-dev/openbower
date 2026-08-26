"use client";

import { useState } from "react";

import { isContentful } from "./output-key.ts";
import {
  AGENT_PROVIDERS,
  AgentOutputSchema,
  MAX_AGENT_OUTPUTS,
  AgentTestResultSchema,
  AgentToolsSchema,
  type AgentConfig,
  type AgentOutput,
  type AgentTestResult,
  type AgentTools,
} from "@bower/api";

// The builder's localStorage custody in ONE place: the key scheme, the
// VALIDATED read, and the best-effort writes. A draft is wire data
// from a past app version, so it is PARSED, never cast: every field
// is optional, and a field whose shape the current app no longer
// understands is DROPPED, not crashed on (a crashing draft would brick
// the builder with Clear draft trapped inside the crashing tree).

export type Provider = AgentConfig["provider"] | "";

export type Draft = {
  /** The agent row's updated_at WHEN the draft was written: a draft
   * against an older save of the row is stale (the row changed in
   * another session) and the server row wins. */
  savedAt?: string;
  label?: string;
  prompt?: string;
  provider?: string;
  source?: string;
  model?: string;
  tools?: AgentTools;
  outputs?: AgentOutput[];
  testRow?: Record<string, string>;
  testResult?: AgentTestResult | null;
  /** The outputs AT RUN TIME: the stored result renders with these,
   * never with the live editor's types. */
  testOutputs?: AgentOutput[];
  testToolsOn?: boolean;
};

/** Only a provider the contract still knows may enter typed state. */
export function draftProvider(value: string | undefined): Provider {
  return value && (AGENT_PROVIDERS as readonly string[]).includes(value) ? (value as Provider) : "";
}

function stringRecord(value: unknown): Record<string, string> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.every(([, v]) => typeof v === "string")) return undefined;
  return Object.fromEntries(entries) as Record<string, string>;
}

/** The tolerant parse, exported pure for its tests: field by field,
 * dropping what fails (the generated schemas judge the structured
 * shapes), never throwing past JSON.parse. */
export function parseDraft(raw: string | null): Draft | null {
  let source: unknown;
  try {
    source = JSON.parse(raw ?? "null");
  } catch {
    return null;
  }
  if (typeof source !== "object" || source === null || Array.isArray(source)) return null;
  const record = source as Record<string, unknown>;
  const draft: Draft = {};
  for (const key of ["savedAt", "label", "prompt", "provider", "source", "model"] as const) {
    if (typeof record[key] === "string") draft[key] = record[key];
  }
  const tools = AgentToolsSchema.safeParse(record.tools);
  if (tools.success) draft.tools = tools.data;
  const testRow = stringRecord(record.testRow);
  if (testRow) draft.testRow = testRow;
  // Capped at the wire bound: a tampered/old draft must not restore
  // more rows than the editor can ever remove back below. Key is
  // DEFAULTED before the parse: a stricter current schema must never
  // discard a whole edit a past bundle wrote validly.
  const keyed = (value: unknown): unknown =>
    Array.isArray(value)
      ? value.map((row) => (typeof row === "object" && row !== null ? { key: "", ...row } : row))
      : value;
  const outputs = AgentOutputSchema.array().safeParse(keyed(record.outputs));
  if (outputs.success && outputs.data.length > 0) draft.outputs = outputs.data.slice(0, MAX_AGENT_OUTPUTS);
  const testOutputs = AgentOutputSchema.array().safeParse(keyed(record.testOutputs));
  if (testOutputs.success) draft.testOutputs = testOutputs.data.slice(0, MAX_AGENT_OUTPUTS);
  const testResult = AgentTestResultSchema.nullable().safeParse(record.testResult);
  if (testResult.success) draft.testResult = testResult.data;
  if (typeof record.testToolsOn === "boolean") draft.testToolsOn = record.testToolsOn;
  return draft;
}

function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([k, v]) => [k, stable(v)]),
    );
  }
  return value;
}

/** Whether two draft payloads describe the same form state (key order
 * insensitive: schema-parsed objects and literals order differently). */
export function draftEquals(a: Draft, b: Draft): boolean {
  return JSON.stringify(stable(a)) === JSON.stringify(stable(b));
}

/** The subset (and trims) SAVE actually sends, ONE projection shared
 * by the request path and the Unsaved banner so the two cannot
 * re-drift (a banner judging raw state showed false positives on a
 * trailing space). */
export function saveShape(draft: Draft): Draft {
  const contentful = (draft.outputs ?? []).filter(isContentful);
  return {
    label: (draft.label ?? "").trim(),
    prompt: (draft.prompt ?? "").trim(),
    provider: draft.provider,
    source: draft.source,
    model: draft.model,
    tools: draft.tools,
    outputs: contentful,
  };
}

function draftKey(agentId?: string): string {
  return `bwr-agent-draft:${agentId ?? "new"}`;
}

/** The draft read once at first render, plus save/clear bound to this
 * agent's key. Storage is best-effort: full or blocked storage must
 * never break the form. */
export function useAgentDraft(agentId?: string): {
  draft: Draft | null;
  save: (draft: Draft) => void;
  clear: () => void;
} {
  const [key] = useState(() => draftKey(agentId));
  const [draft] = useState(() => {
    // Client-only by construction (the builder's ssr:false wrapper).
    try {
      return parseDraft(window.localStorage.getItem(key));
    } catch {
      return null;
    }
  });
  return {
    draft,
    save: (next: Draft) => {
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // best-effort
      }
    },
    clear: () => {
      try {
        window.localStorage.removeItem(key);
      } catch {
        // best-effort
      }
    },
  };
}
