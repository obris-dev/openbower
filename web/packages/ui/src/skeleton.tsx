import { cn } from "./cn";

/** A pulsing placeholder block; size it via className. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-ink/10 dark:bg-paper/10", className)} />;
}
