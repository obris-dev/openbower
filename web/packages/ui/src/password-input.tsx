"use client";

import { forwardRef, type InputHTMLAttributes, useState } from "react";
import { clsx } from "clsx";
import { Eye, EyeOff } from "lucide-react";

export interface PasswordInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  invalid?: boolean;
}

export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(function PasswordInput(
  { invalid, className, ...rest },
  ref,
) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        ref={ref}
        type={show ? "text" : "password"}
        aria-invalid={invalid || undefined}
        className={clsx(
          "w-full rounded-md bg-paper px-3 py-2 pr-10 text-sm text-ink shadow-sm",
          "ring-1 ring-inset ring-ink/15 placeholder:text-ink/40",
          "focus:outline-none focus:ring-2 focus:ring-inset focus:ring-signal",
          "dark:bg-ink-soft dark:text-paper dark:ring-paper/15 dark:placeholder:text-paper/40",
          invalid && "ring-red-500 focus:ring-red-500",
          className,
        )}
        {...rest}
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        className="absolute inset-y-0 right-0 flex items-center px-3 text-ink/50 hover:text-ink/70 dark:text-paper/50 dark:hover:text-paper/70"
      >
        {show ? <EyeOff aria-hidden className="h-4 w-4" /> : <Eye aria-hidden className="h-4 w-4" />}
      </button>
    </div>
  );
});
