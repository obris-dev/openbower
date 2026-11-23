import type { AgentConfig, AgentOutput, AgentTools } from "@bower/api";

/** What the column edit can change: the prompt, the model address, the
 * tools, and each output's description. The output set (keys, names,
 * types) is the sheet's columns and travels through unchanged. */
export type EditDraft = {
  prompt: string;
  provider: AgentConfig["provider"] | "";
  source: string;
  model: string;
  tools: AgentTools;
  outputs: AgentOutput[];
};

export function draftFrom(config: AgentConfig): EditDraft {
  return {
    prompt: config.prompt,
    provider: config.provider,
    source: config.source,
    model: config.model,
    tools: { ...config.tools },
    outputs: config.outputs.map((output) => ({ ...output })),
  };
}

function sameTools(a: AgentTools, b: AgentTools): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]) as Set<keyof AgentTools>;
  return [...keys].every((key) => Boolean(a[key]) === Boolean(b[key]));
}

/** Whether the draft differs from the stored config: Save stays off
 * until something moved. */
export function isDirty(draft: EditDraft, stored: AgentConfig): boolean {
  if (draft.prompt.trim() !== stored.prompt.trim()) return true;
  if (draft.provider !== stored.provider || draft.source !== stored.source || draft.model !== stored.model) {
    return true;
  }
  if (!sameTools(draft.tools, stored.tools)) return true;
  return draft.outputs.some((output, index) => output.description !== (stored.outputs[index]?.description ?? ""));
}

/** The body a save sends, or null while the draft cannot be sent (a
 * blank prompt, no model chosen). */
export function configFrom(draft: EditDraft): AgentConfig | null {
  const prompt = draft.prompt.trim();
  if (!prompt || !draft.provider || !draft.source || !draft.model) return null;
  return {
    prompt,
    provider: draft.provider,
    source: draft.source,
    model: draft.model,
    tools: draft.tools,
    outputs: draft.outputs,
  };
}
