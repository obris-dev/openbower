import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "./cn";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "w-full rounded-md bg-paper px-3 py-2 text-sm text-ink shadow-sm",
        "ring-1 ring-inset placeholder:text-ink/40",
        "focus:outline-none focus:ring-2 focus:ring-inset",
        "dark:bg-ink-soft dark:text-paper dark:placeholder:text-paper/40",
        // Exclusive, not additive: emitting both rings would leave the
        // winner to stylesheet order (and the dark variant to chance).
        invalid
          ? "ring-red-500 focus:ring-red-500 dark:ring-red-500"
          : "ring-ink/15 focus:ring-signal dark:ring-paper/15",
        className,
      )}
      {...rest}
    />
  );
});
