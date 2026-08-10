import { clsx } from "clsx";
import { Loader2 } from "lucide-react";

/** An indeterminate spinner (Lucide Loader2 + spin). Size via className
 * (defaults to h-5 w-5); pair it with visible text or a label, since the
 * icon itself is aria-hidden. */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={clsx("h-5 w-5 animate-spin", className)} />;
}
