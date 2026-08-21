"use client";

import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Popover, PopoverButton, PopoverItem, PopoverPanel } from "@bower/ui";
import type { FolderSummary } from "@bower/api";

import { ConfirmDelete } from "../confirm-delete";

type View = "root" | "move" | "delete";

/** The row's verb menu: verbs first, then a SWAP-IN tier for Move to /
 * the Delete confirm (one panel, mobile-safe, no hover flyouts, no
 * modals). A Popover, not a Menu: the tiers are mixed content, so
 * every control is an ordinary focusable (Tab walks them, clicks only
 * close when a verb completes), while Escape, outside-click, and focus
 * return come from the primitive. The panel unmounts on close, which
 * resets the tier for free. */
export function RowMenu(props: {
  kind: "list" | "folder";
  folders: FolderSummary[];
  currentFolderId?: string;
  containedCount?: number;
  onRename: () => void;
  onMove?: (folderId: string) => void;
  onDelete: () => void;
}) {
  return (
    <Popover className="relative inline-block">
      <PopoverButton aria-label="Row actions" className="rounded p-1 text-faint hover:bg-wash hover:text-foreground">
        <MoreHorizontal aria-hidden className="h-4 w-4" />
      </PopoverButton>
      <PopoverPanel focus>
        {({ close }) => <MenuBody {...props} close={close} />}
      </PopoverPanel>
    </Popover>
  );
}

function MenuBody({
  kind,
  folders,
  currentFolderId,
  containedCount = 0,
  onRename,
  onMove,
  onDelete,
  close,
}: Parameters<typeof RowMenu>[0] & { close: () => void }) {
  const [view, setView] = useState<View>("root");
  const tierRef = useRef<HTMLDivElement>(null);
  const lastView = useRef<View>("root");

  // A tier swap replaces the focused button, which drops focus to
  // <body>; hand it to the new tier (Cancel on the confirm, never the
  // destructive verb), and returning to root refocuses the verb that
  // OPENED the tier.
  useEffect(() => {
    const opener =
      view === "root" && lastView.current !== "root"
        ? tierRef.current?.querySelector<HTMLElement>(`[data-opens="${lastView.current}"]`)
        : null;
    lastView.current = view;
    const target =
      opener ??
      tierRef.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      tierRef.current?.querySelector("button");
    target?.focus();
  }, [view]);

  return (
    <div ref={tierRef} className={view === "delete" ? "w-60" : "w-48"}>
      {view === "root" && (
        <>
          <PopoverItem
            onClick={() => {
              close();
              onRename();
            }}
          >
            Rename
          </PopoverItem>
          {/* Only when a destination exists: a tier with nothing focusable
              strands keyboard focus on body. */}
          {kind === "list" && onMove && (folders.length > 0 || Boolean(currentFolderId)) && (
            <PopoverItem data-opens="move" onClick={() => setView("move")}>
              Move to…
            </PopoverItem>
          )}
          <PopoverItem
            className="text-danger"
            onClick={() => {
              // An EMPTY folder has nothing at stake; deleting it
              // outright beats confirming a no-op.
              if (kind === "folder" && containedCount === 0) {
                close();
                onDelete();
                return;
              }
              setView("delete");
            }}
            data-opens="delete"
          >
            Delete
          </PopoverItem>
        </>
      )}
      {view === "move" && onMove && (
        <>
          <p className="px-4 py-1 text-xs uppercase tracking-wide text-faint">Move to</p>
          <PopoverItem data-autofocus className="text-muted" onClick={() => setView("root")}>
            ← Back
          </PopoverItem>
          {currentFolderId && (
            // The way OUT is also a destination: the root, named the
            // way the rest of the product names it, set apart from the
            // real folders.
            <>
              <PopoverItem
                className="text-muted"
                onClick={() => {
                  close();
                  onMove("");
                }}
              >
                Home
              </PopoverItem>
              <div className="mx-4 my-1 border-t border-hairline" />
            </>
          )}
          <div className="max-h-64 overflow-y-auto">
            {folders
              .filter((f) => f.id !== currentFolderId)
              .map((f) => (
                <PopoverItem
                  key={f.id}
                  onClick={() => {
                    close();
                    onMove(f.id);
                  }}
                >
                  {f.label}
                </PopoverItem>
              ))}
          </div>
        </>
      )}
      {view === "delete" && (
        <div className="px-4 py-1.5">
          <ConfirmDelete
            question={kind === "folder" ? "Delete this folder?" : "Delete this list?"}
            consequence={
              kind === "folder"
                ? containedCount === 1
                  ? "Its list moves back to Home."
                  : `Its ${containedCount.toLocaleString("en-US")} lists move back to Home.`
                : undefined
            }
            onCancel={() => setView("root")}
            onDelete={() => {
              close();
              onDelete();
            }}
          />
        </div>
      )}
    </div>
  );
}
