import type { LabelHTMLAttributes } from "react";
import { clsx } from "clsx";

export function Label({ className, ...rest }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={clsx("block text-sm font-medium text-ink dark:text-paper", className)} {...rest} />;
}
