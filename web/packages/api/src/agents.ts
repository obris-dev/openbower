// Agents: the roster CRUD and the runnable-models catalog. Same
// result-union philosophy as lists.

import {
  AgentCatalogSchema,
  AgentConfigSchema,
  AgentOutputSchema,
  AgentsListSchema,
  AgentSummarySchema,
  AgentToolsSchema,
  RESERVED_OUTPUT_KEYS,
  WIRE_BOUNDS,
  WIRE_CONSTANTS,
  type AgentCatalog,
  type AgentConfig,
  type AgentListItem,
  type AgentOutput,
  type AgentsList,
  type AgentSummary,
} from "@bower/schema";

import { z } from "zod";

import { http, type ApiResult } from "./request.ts";
import { apiRoutes } from "./routes.ts";

export type {
  AgentCatalog,
  AgentConfig,
  AgentListItem,
  AgentOutput,
  AgentsList,
  AgentSummary,
};
export type { AgentTools } from "@bower/schema";
export { AgentToolsSchema };
// Re-exported so app code never imports @bower/schema directly (the
// schema package has exactly one consumer: this one).
export type { CatalogModel } from "@bower/schema";

// Enum OPTIONS derive from the generated contract: a type added or
// removed Python-side reaches every consumer through the regen, never
// through a hand-retyped list.
export const AGENT_PROVIDERS = AgentConfigSchema.shape.provider.options;
export const AGENT_OUTPUT_TYPES = AgentOutputSchema.shape.type.options;
// The tool key set, straight off the wire's typed sub-model
// (closed on the request leg; reads tolerate unknown keys).
export const AGENT_TOOL_KEYS = Object.keys(AgentToolsSchema.shape) as (keyof typeof AgentToolsSchema.shape)[];
export type AgentToolKey = (typeof AGENT_TOOL_KEYS)[number];
// Size bounds, straight off the contract document (the server's
// serializers enforce the same numbers; nothing here is invented).
export const MAX_AGENT_OUTPUTS = WIRE_BOUNDS.AgentConfig.outputs.maxItems;
export const AGENT_PROMPT_MAX_LENGTH = WIRE_BOUNDS.AgentConfig.prompt.maxLength;
export const AGENT_LABEL_MAX_LENGTH = WIRE_BOUNDS.AgentSummary.label.maxLength;
export const AGENT_OUTPUT_KEY_MAX_LENGTH = WIRE_BOUNDS.AgentOutput.key.maxLength;
export const AGENT_OUTPUT_LABEL_MAX_LENGTH = WIRE_BOUNDS.AgentOutput.label.maxLength;
export const AGENT_OUTPUT_DESCRIPTION_MAX_LENGTH = WIRE_BOUNDS.AgentOutput.description.maxLength;
// The server's refused output keys, off the contract document.
export { RESERVED_OUTPUT_KEYS };
// Output keys may not CONTAIN this either: answers carry
// `<key>_bwr_confidence_reason` and `<key>_bwr_confidence` companions
// per output, so the namespace is reserved (a user's own "Confidence"
// output stays legal).
export const RESERVED_OUTPUT_MARKER = WIRE_CONSTANTS.RESERVED_OUTPUT_MARKER;
// Validators for client-restored state (drafts are wire data from a
// past app version; parse, never cast).
export { AgentOutputSchema };

export async function fetchAgents(): Promise<ApiResult<AgentsList>> {
  return http.get(apiRoutes.agents.index, AgentsListSchema);
}

export async function fetchAgent(id: string): Promise<ApiResult<AgentSummary>> {
  return http.get(apiRoutes.agents.detail(id), AgentSummarySchema);
}

export async function createAgent(label: string, config: AgentConfig): Promise<ApiResult<AgentSummary>> {
  return http.post(apiRoutes.agents.index, AgentSummarySchema, { label, config });
}

export async function updateAgent(
  id: string,
  patch: { label?: string; config?: AgentConfig },
): Promise<ApiResult<AgentSummary>> {
  return http.patch(apiRoutes.agents.detail(id), AgentSummarySchema, patch);
}

export async function deleteAgent(id: string): Promise<ApiResult<null>> {
  return http.delete(apiRoutes.agents.detail(id));
}

// The providers this bundle can NAME, off the contract (never
// hand-retyped): the tolerant read below maps anything else to null.
export const SEARCH_PROVIDER_CHOICES = WIRE_CONSTANTS.SEARCH_PROVIDER_CHOICES;
const KNOWN_SEARCH_PROVIDER_CHOICES = new Set<string>(SEARCH_PROVIDER_CHOICES);

// The catalog's provider is a server-owned enum, so the read is TOLERANT:
// a strict parse would fail the WHOLE catalog for every deployed
// bundle the day a third provider ships, and the picker it feeds would
// render an unloadable catalog with a Retry that can never succeed.
export const TolerantAgentCatalogSchema = AgentCatalogSchema.extend({
  search_provider: z.string().nullable().default(null),
});

/** A provider this bundle has never heard of reads as null, the value
 * that promises LEAST: the copy composing it names the paid provider only
 * where it IS the remedy, and a provider we cannot name is not one. */
export function knownSearchProvider(provider: string | null): AgentCatalog["search_provider"] {
  return provider !== null && KNOWN_SEARCH_PROVIDER_CHOICES.has(provider) ? (provider as AgentCatalog["search_provider"]) : null;
}

export async function fetchAgentCatalog(): Promise<ApiResult<AgentCatalog>> {
  const res = await http.get(apiRoutes.agents.catalog, TolerantAgentCatalogSchema);
  if (res.status !== "ok") return res;
  return { ...res, data: { ...res.data, search_provider: knownSearchProvider(res.data.search_provider) } };
}
