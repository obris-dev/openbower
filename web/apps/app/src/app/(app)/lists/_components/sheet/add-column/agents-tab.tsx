"use client";

import { Button } from "@bower/ui";
import type { AgentListItem } from "@bower/api";

import { AGENT_TOOLS } from "../../../../_components/agent-config";

/** The saved-agents picker: slim rows (name, model, tools) selecting
 * the agent the fill runs. The list endpoint ships only what the rows
 * draw; the drawer fetches the selected agent's full config separately
 * for its facts. */
export function AgentsTab({
  agents,
  failed,
  onRetry,
  selectedId,
  onSelect,
  onWritePrompt,
}: {
  agents: AgentListItem[] | null;
  failed: boolean;
  onRetry: () => void;
  selectedId: string;
  onSelect: (id: string) => void;
  onWritePrompt: () => void;
}) {
  if (agents === null && !failed) {
    return <p className="text-xs text-faint">Loading your agents…</p>;
  }
  if (agents === null) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-danger">Your agents could not be loaded.</p>
        <Button size="sm" variant="outline" type="button" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (agents.length === 0) {
    return (
      <div className="space-y-2 rounded-lg border border-hairline p-4">
        <p className="text-sm text-muted">
          No saved agents yet. An agent is a prompt you save once and reuse across sheets; build one on the Agents
          page, or write this column&rsquo;s prompt directly.
        </p>
        <Button size="sm" variant="outline" type="button" onClick={onWritePrompt}>
          Write a new prompt
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {agents.map((agent) => {
        const selected = agent.id === selectedId;
        const tools = AGENT_TOOLS.filter(({ key }) => agent.tools[key]).map(({ label }) => label);
        return (
          <button
            key={agent.id}
            type="button"
            aria-pressed={selected}
            onClick={() => onSelect(agent.id)}
            className={
              selected
                ? "w-full rounded-lg border border-signal bg-wash p-3 text-left"
                : "w-full rounded-lg border border-hairline p-3 text-left hover:bg-wash"
            }
          >
            <span className="flex min-w-0 items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium text-foreground" title={agent.label}>
                {agent.label}
              </span>
              <span className="min-w-0 max-w-40 shrink-0 truncate text-xs text-muted" title={agent.model}>
                {agent.model}
              </span>
            </span>
            <span className="mt-0.5 block text-xs text-muted">{tools.length > 0 ? tools.join(" | ") : "No tools"}</span>
          </button>
        );
      })}
    </div>
  );
}
