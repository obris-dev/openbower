// Agents: the roster CRUD, the runnable-models catalog, and the test
// bench. Same result-union philosophy as lists.

import {
  AgentCatalogSchema,
  AgentConfigSchema,
  AgentOutputSchema,
  AgentsListSchema,
  AgentSummarySchema,
  AgentTestResultSchema,
  AgentTestRunSchema,
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
  type AgentTestResult,
  type AgentTestRun,
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
  AgentTestResult,
  AgentTestRun,
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
// The test POST's 409 code, mirroring the server's constant
// (agents/views.py TEST_RUN_ACTIVE_CODE): the one refusal a client
// classifies by code rather than status alone.
export const TEST_RUN_ACTIVE_CODE = "test_run_active";
// The server's refused output keys and the bench row cap, off the
// contract document.
export { RESERVED_OUTPUT_KEYS };
// Output keys may not CONTAIN this either: answers carry
// `<key>_bwr_confidence_reason` and `<key>_bwr_confidence` companions
// per output, so the namespace is reserved (a user's own "Confidence"
// output stays legal).
export const RESERVED_OUTPUT_MARKER = WIRE_CONSTANTS.RESERVED_OUTPUT_MARKER;
export const TEST_ROW_MAX_KEYS = WIRE_CONSTANTS.TEST_ROW_MAX_KEYS;
// Validators for client-restored state (drafts are wire data from a
// past app version; parse, never cast).
export { AgentOutputSchema, AgentTestResultSchema };

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

// The doors this bundle can NAME, off the contract (never
// hand-retyped): the tolerant read below maps anything else to null.
export const SEARCH_DOORS = WIRE_CONSTANTS.SEARCH_DOORS;
const KNOWN_SEARCH_DOORS = new Set<string>(SEARCH_DOORS);

// The catalog's door is a server-owned enum, so the read is TOLERANT:
// a strict parse would fail the WHOLE catalog for every deployed
// bundle the day a third door ships, and the picker it feeds would
// render an unloadable catalog with a Retry that can never succeed.
export const TolerantAgentCatalogSchema = AgentCatalogSchema.extend({
  search_provider: z.string().nullable().default(null),
});

/** A door this bundle has never heard of reads as null, the value
 * that promises LEAST: the copy composing it names the paid door only
 * where it IS the remedy, and a door we cannot name is not one. */
export function knownSearchDoor(door: string | null): AgentCatalog["search_provider"] {
  return door !== null && KNOWN_SEARCH_DOORS.has(door) ? (door as AgentCatalog["search_provider"]) : null;
}

export async function fetchAgentCatalog(): Promise<ApiResult<AgentCatalog>> {
  const res = await http.get(apiRoutes.agents.catalog, TolerantAgentCatalogSchema);
  if (res.status !== "ok") return res;
  return { ...res, data: { ...res.data, search_provider: knownSearchDoor(res.data.search_provider) } };
}

/** Start a test run of a DRAFTED config (saved or not) against one
 * hand-fed row. Returns the run to POLL: the agentic loop (the model
 * drives its own bounded tool calls, then answers) takes seconds to
 * minutes, and the bench must not hold a connection open for it. A 409 (another run is live) surfaces
 * through the funnel as its server-written detail; deliberately NOT
 * resumable (adopting another run would render its cells under this
 * config's types and diagnoses). */
export async function testAgent(config: AgentConfig, row: Record<string, string>): Promise<ApiResult<AgentTestRun>> {
  return http.post(apiRoutes.agents.test, AgentTestRunSchema, { config, row });
}

/** The poll leg: the run's status, and its result once complete. */
export async function fetchTestRun(id: string): Promise<ApiResult<AgentTestRun>> {
  return http.get(apiRoutes.agents.testRun(id), AgentTestRunSchema);
}
