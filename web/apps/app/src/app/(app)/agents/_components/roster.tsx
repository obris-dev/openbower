"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, EmptyState, useToast } from "@bower/ui";
import { deleteAgent, fetchAgents, webRoutes, type AgentListItem } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { LinkButton } from "../../../_components/link-button";
import { AgentRowMenu } from "./row-menu";
import { AGENT_TOOLS } from "./tools-meta";

function day(iso: string): string {
  // Pinned locale + UTC: server-rendered AND hydrated; disagreement is
  // a hydration error.
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** The roster in the directory table idiom: name, model, tools,
 * created; row menus for the occasional verbs; the builder owns
 * everything else. */
export function Roster({ initialAgents }: { initialAgents: AgentListItem[] }) {
  const toast = useToast();
  const [agents, setAgents] = useState(initialAgents);

  async function refresh() {
    const res = await fetchAgents();
    if (!ensureOk(res, toast)) return;
    setAgents(res.data.items);
  }

  async function remove(agent: AgentListItem) {
    const res = await deleteAgent(agent.id);
    if (!ensureOk(res, toast)) return;
    toast.success("Agent deleted.", agent.label);
    await refresh();
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Agents</h1>
          {agents.length === 0 && (
            <p className="mt-1 text-sm text-muted">Reusable research prompts you test once and point at sheets.</p>
          )}
        </div>
        <LinkButton href={webRoutes.agentNew}>New agent</LinkButton>
      </div>

      {agents.length === 0 ? (
        <EmptyState
          title="No agents yet"
          subtitle="An agent is a prompt that runs per row: build one, test it on a single row, then reuse it across sheets."
        >
          <LinkButton size="sm" href={webRoutes.agentNew}>
            Build your first agent
          </LinkButton>
        </EmptyState>
      ) : (
        <Card className="p-0">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-faint">
                <th className="w-full py-3 pl-4 pr-4 font-medium">Name</th>
                <th className="hidden px-4 py-3 font-medium sm:table-cell">Model</th>
                <th className="hidden px-4 py-3 font-medium md:table-cell">Tools</th>
                <th className="hidden px-4 py-3 font-medium sm:table-cell">Created</th>
                <th className="w-10 py-3 pl-2 pr-3" aria-label="Actions" />
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {agents.map((agent) => {
                const tools = AGENT_TOOLS.filter(({ key }) => agent.tools[key]).map(({ label }) => label);
                return (
                  <tr key={agent.id} className="align-middle">
                    <td className="w-full max-w-0 py-2.5 pl-4 pr-4">
                      <Link
                        href={webRoutes.agent(agent.id)}
                        title={agent.label}
                        className="block truncate font-medium text-foreground hover:text-signal"
                      >
                        {agent.label}
                      </Link>
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted sm:table-cell">
                      <span className="block max-w-48 truncate" title={agent.model}>
                        {agent.model}
                      </span>
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted md:table-cell">
                      {tools.length > 0 ? tools.join(" | ") : "None"}
                    </td>
                    <td className="hidden whitespace-nowrap px-4 py-2.5 text-muted sm:table-cell">
                      {day(agent.created_at)}
                    </td>
                    <td className="py-2.5 pl-2 pr-3 text-right">
                      <AgentRowMenu editHref={webRoutes.agent(agent.id)} onDelete={() => void remove(agent)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
