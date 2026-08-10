import { forwardRef, type InputHTMLAttributes } from "react";
import { clsx } from "clsx";

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={clsx(
        "w-full rounded-md bg-paper px-3 py-2 text-sm text-ink shadow-sm",
        "ring-1 ring-inset ring-ink/15 placeholder:text-ink/40",
        "focus:outline-none focus:ring-2 focus:ring-inset focus:ring-signal",
        "dark:bg-ink-soft dark:text-paper dark:ring-paper/15 dark:placeholder:text-paper/40",
        invalid && "ring-red-500 focus:ring-red-500",
        className,
      )}
      {...rest}
    />
  );
});
