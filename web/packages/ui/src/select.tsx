import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean;
  /** Incomplete, not wrong: amber ring, no aria-invalid (red stays
   * reserved for genuine rejections). */
  warned?: boolean;
}

/** Native select styled to sit beside Input (same surface, ring, and
 * focus treatment); the platform picker beats a rebuilt listbox for
 * plain single-choice fields. `className` sizes the wrapper: the
 * chevron must anchor to the control's box, and appearance-none
 * removed the native arrow it replaces. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { invalid, warned, className, ...rest },
  ref,
) {
  return (
    <div className={cn("relative", className)}>
      <select
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(
          "w-full appearance-none rounded-md bg-surface px-3 py-2 pr-8 text-sm text-foreground shadow-sm",
          "ring-1 ring-inset",
          "focus:outline-none focus:ring-2 focus:ring-inset",
          // Exclusive, not additive: emitting both rings would leave the
          // winner to stylesheet order (and the dark variant to chance).
          invalid
            ? "ring-danger-edge focus:ring-danger-edge"
            : warned
              ? "ring-warning-edge focus:ring-warning-edge"
              : "ring-edge focus:ring-signal",
        )}
        {...rest}
      />
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-faint"
      />
    </div>
  );
});
