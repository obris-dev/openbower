"use client";

import { Globe, Users } from "lucide-react";
import { Button, Card, Switch } from "@bower/ui";
import type { AgentCatalog, AgentTools } from "@bower/api";

import { AGENT_TOOLS, type AgentToolKey } from "../tools-meta";

// Per-tool doors: web search runs through ANY usable provider; finding
// contacts requires DataForSEO specifically (LinkedIn profile searches
// need Google-grade results). Keys and labels come from the ONE tool
// registry; this map adds only what the toggles alone need.
const TOOL_DETAIL: Record<AgentToolKey, { icon: typeof Globe; hint: string; ready: (c: AgentCatalog) => boolean }> = {
  web_search: {
    icon: Globe,
    hint: "Search the web for more context before answering.",
    ready: (catalog) => catalog.search_available,
  },
  find_contacts: {
    icon: Users,
    hint: "Find people's LinkedIn profiles matching the prompt; links stay grounded in evidence.",
    ready: (catalog) => catalog.contacts_available,
  },
};

/** Tool toggles with the SETUP WORKFLOW inline: a tool whose search
 * door isn't ready is gated, and the card walks through the DataForSEO
 * setup right there instead of quietly disabling. */
export function ToolToggles({
  catalog,
  failed,
  onRetry,
  tools,
  onChange,
}: {
  catalog: AgentCatalog | null;
  failed: boolean;
  onRetry: () => void;
  tools: AgentTools;
  onChange: (next: AgentTools) => void;
}) {
  const searchReady = catalog?.search_available ?? false;
  const contactsReady = catalog?.contacts_available ?? false;
  return (
    <Card className="space-y-3 p-4">
      <p className="text-xs font-semibold uppercase tracking-wide text-faint">Tools</p>
      {/* A catalog consumer like the picker: a failed fetch must not
          strand the switches disabled with no message and no way
          back, and loading says so. */}
      {catalog === null && !failed && <p className="text-xs text-faint">Loading tool availability…</p>}
      {catalog === null && failed && (
        <>
          <p className="text-xs text-danger">Tool availability could not be loaded.</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </>
      )}
      {AGENT_TOOLS.map((tool) => {
        const detail = TOOL_DETAIL[tool.key];
        const ready = catalog !== null && detail.ready(catalog);
        return (
          <div key={tool.key} className="flex items-start gap-3">
            <detail.icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-faint" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">{tool.label}</p>
              <p className="text-xs text-muted">{detail.hint}</p>
            </div>
            <Switch
              checked={tools[tool.key]}
              onChange={(checked) => {
                const next = { ...tools };
                next[tool.key] = checked;
                onChange(next);
              }}
              aria-label={tool.label}
              disabled={!ready && !tools[tool.key]}
              className="mt-0.5"
            />
          </div>
        );
      })}
      {catalog !== null && !contactsReady && (
        <div className="space-y-1.5 rounded-md border border-warning-hairline bg-warning-wash px-3 py-2 text-xs">
          <p className="font-medium text-warning">
            {searchReady
              ? "Contact search isn't set up yet: finding contacts needs DataForSEO (LinkedIn profile searches need Google-grade results). Web search works out of the box through the free search provider."
              : `Search is misconfigured on this deployment: the chosen provider is missing its credentials; ${catalog.support_followup}. For contact search, set up DataForSEO:`}
          </p>
          <ol className="list-decimal space-y-0.5 pl-4 text-muted">
            <li>
              Create a{" "}
              <a
                href="https://app.dataforseo.com/register"
                target="_blank"
                rel="noreferrer"
                className="text-signal hover:underline"
              >
                DataForSEO account
              </a>{" "}
              ($1 trial credit, pay as you go, no expiry).
            </li>
            <li>
              Add DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD to the deployment&rsquo;s environment (the block is in
              its .env.example); if someone else runs it, send them this step.
            </li>
            <li>Restart the deployment and reload this page.</li>
          </ol>
        </div>
      )}
    </Card>
  );
}
