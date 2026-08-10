import type { HTMLAttributes } from "react";
import { clsx } from "clsx";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx("rounded-lg bg-paper shadow dark:bg-ink-soft", className)}
      {...rest}
    />
  );
}
