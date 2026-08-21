import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  /** Incomplete, not wrong: amber ring, no aria-invalid (red stays
   * reserved for genuine rejections). */
  warned?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, warned, className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "w-full rounded-md bg-surface px-3 py-2 text-sm text-foreground shadow-sm",
        "ring-1 ring-inset placeholder:text-faint",
        "focus:outline-none focus:ring-2 focus:ring-inset",
        // Exclusive, not additive: emitting both rings would leave the
        // winner to stylesheet order (and the dark variant to chance).
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
