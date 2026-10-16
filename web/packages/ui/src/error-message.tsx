import type { ReactNode } from "react";

import { cn } from "./cn";

/** The form-level error banner. `message` may carry markup, so a line
 * whose fix lives elsewhere can link its own verb instead of growing a
 * second line in another color. */
export function ErrorMessage({ message, className, id }: { message: ReactNode; className?: string; id?: string }) {
  return (
    <div
      role="alert"
      id={id}
      className={cn(
        "rounded-md bg-danger-wash p-3 text-sm text-danger-strong",
        className,
      )}
    >
      {message}
    </div>
  );
}
