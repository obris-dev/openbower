"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { MoreHorizontal } from "lucide-react";
import { Popover, PopoverButton, PopoverItem, PopoverPanel } from "@bower/ui";

import { ConfirmDelete } from "../../_components/confirm-delete";
import { DELETE_AGENT_CONSEQUENCE, DELETE_AGENT_QUESTION } from "./copy";

/** The roster row's verbs: Edit opens the builder (which owns rename
 * and everything else); Delete confirms in a swap-in tier, the
 * directory pattern. */
export function AgentRowMenu({ editHref, onDelete }: { editHref: string; onDelete: () => void }) {
  return (
    <Popover className="relative inline-block">
      <PopoverButton aria-label="Agent actions" className="rounded p-1 text-faint hover:bg-wash hover:text-foreground">
        <MoreHorizontal aria-hidden className="h-4 w-4" />
      </PopoverButton>
      <PopoverPanel focus>
        {({ close }) => <MenuBody editHref={editHref} onDelete={onDelete} close={close} />}
      </PopoverPanel>
    </Popover>
  );
}

function MenuBody({ editHref, onDelete, close }: { editHref: string; onDelete: () => void; close: () => void }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div className="w-60 px-4 py-1.5">
        <ConfirmDelete
          question={DELETE_AGENT_QUESTION}
          consequence={DELETE_AGENT_CONSEQUENCE}
          onCancel={() => setConfirming(false)}
          onDelete={() => {
            close();
            onDelete();
          }}
        />
      </div>
    );
  }
  return (
    <div className="w-48">
      <PopoverItem
        onClick={() => {
          close();
          router.push(editHref);
        }}
      >
        Edit
      </PopoverItem>
      <PopoverItem className="text-danger" onClick={() => setConfirming(true)}>
        Delete
      </PopoverItem>
    </div>
  );
}
