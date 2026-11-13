"use client";

import { Zap } from "lucide-react";
import { Label, Popover, PopoverButton, PopoverPanel, Switch, buttonClasses, cn } from "@bower/ui";

const SWITCH_ID = "sheet-autofill";

const AUTOFILL_LABEL = "Autofill";
const AUTOFILL_ON = "On";
const AUTOFILL_OFF = "Off";
const AUTOFILL_SWITCH = "Fill AI columns as rows arrive";
const AUTOFILL_HINT =
  "When it's off, AI columns stay empty on new rows until you fill them. Turning it back on doesn't fill the rows that arrived while it was off.";

/** The sheet's autofill, where a person sees it without opening anything:
 * the toolbar button says On or Off in words, so its name carries the state
 * to a screen reader and never rests on the bolt's colour alone, and its
 * panel holds the switch and what it decides. */
export function AutofillControl({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <Popover className="relative">
      <PopoverButton className={buttonClasses({ variant: "outline", size: "sm" }, "gap-1.5")}>
        <Zap aria-hidden className={cn("h-4 w-4", on ? "text-signal" : "text-faint")} />
        {AUTOFILL_LABEL}{" "}
        <span className="font-medium text-muted">{on ? AUTOFILL_ON : AUTOFILL_OFF}</span>
      </PopoverButton>
      <PopoverPanel className="w-80 space-y-2 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor={SWITCH_ID} className="text-sm font-medium text-foreground">
            {AUTOFILL_SWITCH}
          </Label>
          <Switch id={SWITCH_ID} checked={on} onChange={onChange} />
        </div>
        <p className="text-xs text-muted">{AUTOFILL_HINT}</p>
      </PopoverPanel>
    </Popover>
  );
}
