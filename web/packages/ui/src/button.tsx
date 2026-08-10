import type { ButtonHTMLAttributes } from "react";
import { clsx } from "clsx";

type Variant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  fullWidth?: boolean;
  // When set, render an <a> styled as the button (for navigation links), so
  // callers don't nest a <button> inside an <a> (invalid HTML). loading /
  // disabled don't apply to the link form.
  href?: string;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-signal text-paper hover:bg-signal-600 disabled:bg-signal-300",
  secondary: "bg-ink/5 text-ink hover:bg-ink/10 dark:bg-paper/10 dark:text-paper dark:hover:bg-paper/20",
  outline: "border border-ink/20 text-ink hover:bg-ink/5 dark:border-paper/20 dark:text-paper dark:hover:bg-paper/10",
  ghost: "text-ink hover:bg-ink/5 dark:text-paper dark:hover:bg-paper/10",
  danger: "bg-red-600 text-white hover:bg-red-700",
};

const SIZES: Record<Size, string> = {
  sm: "px-3 py-1.5 text-sm",
  md: "px-4 py-2 text-sm",
  lg: "px-5 py-2.5 text-base",
};

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  fullWidth = false,
  href,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = clsx(
    "inline-flex items-center justify-center rounded-md font-semibold transition-colors",
    "focus:outline-none focus-visible:ring-2 focus-visible:ring-signal focus-visible:ring-offset-2",
    "disabled:cursor-not-allowed disabled:opacity-70",
    VARIANTS[variant],
    SIZES[size],
    fullWidth && "w-full",
    className,
  );
  if (href) {
    // Link form: a navigation button. rest (ButtonHTMLAttributes) is NOT
    // spread here, its handlers are typed for a button element and don't fit
    // an anchor; link-style Buttons take href + children only. Pass an
    // onClick/aria via a wrapping element if a nav link ever needs them.
    return (
      <a href={href} className={classes}>
        {children}
      </a>
    );
  }
  return (
    <button {...rest} disabled={disabled || loading} aria-busy={loading || undefined} className={classes}>
      {children}
    </button>
  );
}
