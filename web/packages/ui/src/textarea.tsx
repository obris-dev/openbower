import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "./cn";

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
  /** Incomplete, not wrong: amber ring, no aria-invalid (red stays
   * reserved for genuine rejections). */
  warned?: boolean;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { invalid, warned, className, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "w-full rounded-md bg-surface px-3 py-2 text-sm text-foreground shadow-sm",
        "ring-1 ring-inset placeholder:text-faint",
        "focus:outline-none focus:ring-2 focus:ring-inset",
        // Exclusive, not additive (see Input): emitting both rings would
        // leave the winner to stylesheet order.
        invalid
          ? "ring-danger-edge focus:ring-danger-edge"
          : warned
            ? "ring-warning-edge focus:ring-warning-edge"
            : "ring-edge focus:ring-signal",
        className,
      )}
      {...rest}
    />
  );
});
