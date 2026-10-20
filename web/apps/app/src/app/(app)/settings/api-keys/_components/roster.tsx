"use client";

import { useRef, useState } from "react";
import { Plus } from "lucide-react";
import { Button, Card, EmptyState, Popover, PopoverButton, PopoverPanel, useToast } from "@bower/ui";
import { revokeApiKey, webRoutes, type ApiKeyWire } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { Breadcrumbs } from "../../../_components/breadcrumbs";
import { ConfirmDelete } from "../../../_components/confirm-delete";
import { formatTime } from "../../../_components/webhook-delivery";
import { CreateKeyDrawer } from "./create-key-drawer";
import { CREATE_ACTION, EMPTY_TITLE, REVOKE_QUESTION, USAGE_HINT } from "./copy";

/** The keys as rows, the create drawer, and the revoke ritual (one key
 * at a time, owned here). A created key is prepended from the drawer's
 * response; a revoked one leaves the list, since the service lists
 * live keys only. */
export function Roster({ initialKeys }: { initialKeys: ApiKeyWire[] }) {
  const toast = useToast();
  const [keys, setKeys] = useState(initialKeys);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  // The revoked row takes its trigger with it, so focus moves to the
  // page's own action instead of dropping to the document.
  const createAction = useRef<HTMLDivElement>(null);

  async function revoke(id: string) {
    setRevokingId(id);
    const res = await revokeApiKey(id);
    setRevokingId(null);
    if (!ensureOk(res, toast, { title: "Key not revoked" })) return;
    setKeys((prev) => prev.filter((key) => key.id !== id));
    createAction.current?.querySelector("button")?.focus();
  }

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-2">
          <Breadcrumbs trail={[{ label: "Settings", href: webRoutes.settings }]} />
          <h1 className="text-2xl font-bold text-foreground">API keys</h1>
          <p className="text-sm text-muted">{USAGE_HINT}</p>
        </div>
        <div ref={createAction}>
          <Button onClick={() => setDrawerOpen(true)}>
            <Plus aria-hidden className="mr-1.5 h-4 w-4" />
            {CREATE_ACTION}
          </Button>
        </div>
      </div>

      {keys.length === 0 ? (
        <EmptyState title={EMPTY_TITLE}>
          <Button size="sm" onClick={() => setDrawerOpen(true)}>
            {CREATE_ACTION}
          </Button>
        </EmptyState>
      ) : (
        <Card className="divide-y divide-hairline p-0">
          {keys.map((key) => (
            <div key={key.id} className="flex items-center gap-3 px-5 py-4">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{key.name}</p>
                <p className="text-xs text-muted">
                  <span className="font-mono">…{key.last_four}</span>
                  <span className="mx-2">|</span>
                  Created {formatTime(key.created_at)}
                </p>
              </div>
              {/* The question hangs off the verb in an anchored panel, the
                  agents roster's pattern: the row never changes shape. */}
              <Popover className="relative inline-block">
                <PopoverButton as={Button} size="sm" variant="ghost" className="text-danger">
                  Revoke
                </PopoverButton>
                <PopoverPanel focus>
                  {({ close }) => (
                    <div className="w-64 px-4 py-1.5">
                      <ConfirmDelete
                        verb="Revoke"
                        question={REVOKE_QUESTION}
                        busy={revokingId === key.id}
                        onCancel={close}
                        onDelete={() => void revoke(key.id)}
                      />
                    </div>
                  )}
                </PopoverPanel>
              </Popover>
            </div>
          ))}
        </Card>
      )}

      <CreateKeyDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        onCreated={(key) => setKeys((prev) => [key, ...prev])}
      />
    </>
  );
}
