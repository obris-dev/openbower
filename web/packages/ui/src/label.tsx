import type { LabelHTMLAttributes } from "react";
import { cn } from "./cn";

export function Label({ className, ...rest }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={cn("block text-sm font-medium text-foreground", className)} {...rest} />;
}
