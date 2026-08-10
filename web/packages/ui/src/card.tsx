import type { HTMLAttributes } from "react";
import { cn } from "./cn";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-lg bg-paper shadow dark:bg-ink-soft", className)}
      {...rest}
    />
  );
}
