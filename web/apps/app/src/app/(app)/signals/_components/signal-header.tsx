import type { ReactNode } from "react";
import { webRoutes } from "@bower/api";

import { LinkButton } from "../../../_components/link-button";
import { Breadcrumbs } from "../../_components/breadcrumbs";
import { TITLE as SIGNALS_TITLE, VIEW_SHEET_ACTION } from "./copy";

/** A signal page's head: back to Signals, the title (focusable, so focus
 * has somewhere to land once the control that had it is gone), a line
 * under it, and, for a saved signal, the way to its sheet. One header for
 * Create signal, a signal's settings, and what is left of a gone one. */
export function SignalHeader({
  title,
  sheetId,
  children,
}: {
  title: string;
  /** The signal's sheet, once there is one. */
  sheetId?: string;
  /** The line under the title. */
  children?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <Breadcrumbs trail={[{ label: SIGNALS_TITLE, href: webRoutes.signals }]} />
        <h1 tabIndex={-1} className="mt-1 truncate text-2xl font-bold text-foreground outline-none">
          {title}
        </h1>
        {children}
      </div>
      {sheetId !== undefined && (
        <LinkButton size="sm" variant="outline" href={webRoutes.sheet(sheetId)}>
          {VIEW_SHEET_ACTION}
        </LinkButton>
      )}
    </div>
  );
}
