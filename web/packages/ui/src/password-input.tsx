"use client";

import { forwardRef, type InputHTMLAttributes, useState } from "react";
import { cn } from "./cn";
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
        className={cn(
          "w-full rounded-md bg-surface px-3 py-2 pr-10 text-sm text-foreground shadow-sm",
          "ring-1 ring-inset placeholder:text-faint",
          "focus:outline-none focus:ring-2 focus:ring-inset",
          invalid ? "ring-red-500 focus:ring-red-500" : "ring-edge focus:ring-signal",
          className,
        )}
        {...rest}
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        className="absolute inset-y-0 right-0 flex items-center px-3 text-muted hover:text-foreground"
      >
        {show ? <EyeOff aria-hidden className="h-4 w-4" /> : <Eye aria-hidden className="h-4 w-4" />}
      </button>
    </div>
  );
});
