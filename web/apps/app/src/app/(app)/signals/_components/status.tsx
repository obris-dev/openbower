import { cn } from "@bower/ui";
import type { LogStatus } from "@bower/api";

import { STATUS_PILL } from "./copy";

const TONE = {
  quiet: "text-muted",
  warning: "text-warning",
  danger: "text-danger",
} as const;

/** Where a signal stands, as a pill; a status this bundle cannot name
 * renders nothing. */
export function SignalStatus({ status }: { status: LogStatus }) {
  const pill = STATUS_PILL[status];
  if (!pill) return null;
  return <span className={cn("shrink-0 rounded-full bg-wash px-2 py-0.5 text-xs", TONE[pill.tone])}>{pill.label}</span>;
}
