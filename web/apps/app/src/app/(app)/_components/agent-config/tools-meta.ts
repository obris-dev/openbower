import { AGENT_TOOL_KEYS, type AgentTools, AgentToolsSchema, type AgentToolKey } from "@bower/api";

export type { AgentToolKey };

// Display copy for each wire tool key. Typed against the DERIVED key
// union: a tool added server-side reaches here through the regen, and
// tsc refuses to build until it gets a label.
const TOOL_LABELS: Record<AgentToolKey, string> = {
  web_search: "Web search",
  find_contacts: "Find contacts",
};

// Display ORDER is a product decision, never a JSON-sort artifact
// (the generated shape lists find_contacts first; the card, the
// roster chips, and the copy all narrate web search first).
// Pinned by tools-meta.test.ts as SET-EQUAL to the wire keys: a wire
// tool missing here would silently vanish from every surface.
export const TOOL_ORDER: readonly AgentToolKey[] = ["web_search", "find_contacts"];

// The ONE tool registry the agents list, the toggles, and the
// builder's empty state all read: keys come from the contract's
// AgentTools shape (closed on the request leg; reads tolerate), never
// hand-retyped.
export const AGENT_TOOLS = TOOL_ORDER.map((key) => ({ key, label: TOOL_LABELS[key] }));

// The schema's own defaults ARE the empty state.
export const EMPTY_TOOLS: AgentTools = AgentToolsSchema.parse({});
