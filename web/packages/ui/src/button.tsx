"use client";

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { Button as HeadlessButton, type ButtonProps as HeadlessButtonProps } from "@headlessui/react";
import { cn } from "./cn";

type Variant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

type CommonProps = {
  variant?: Variant;
  size?: Size;
  fullWidth?: boolean;
  className?: string;
  children: ReactNode;
};

// Discriminated by href: the link form takes ANCHOR props (adapted from
// the reference kit's shape), so a nav button can carry onClick/aria
// instead of silently dropping them; loading applies to the button form
// only (a navigation cannot be in-flight the same way).
export type ButtonProps = CommonProps &
  (
    | ({ href: string; loading?: never } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "className" | "href">)
    | ({ href?: never; loading?: boolean } & Omit<HeadlessButtonProps, "as" | "className" | "children">)
  );

const VARIANTS: Record<Variant, string> = {
  // Dark-on-brand, not white-on-brand: the teal is too light to carry
  // white text at WCAG AA (about 2.4:1); deep-teal text reads at 5.2:1
  // and keeps the brand hue as the surface (the reference kit treats
  // its light brand colors the same way). Disabled relies on the shared
  // opacity dim, which keeps the text/surface pair intact.
  primary: "bg-signal text-signal-900 hover:bg-signal-400",
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

/** Expand the hit area to at least 44x44px on touch devices (pointer
 * precision is the media feature; mice keep the visual bounds). */
function TouchTarget({ children }: { children: ReactNode }) {
  return (
    <>
      <span
        aria-hidden
        className="absolute left-1/2 top-1/2 size-[max(100%,2.75rem)] -translate-x-1/2 -translate-y-1/2 pointer-fine:hidden"
      />
      {children}
    </>
  );
}

export function Button({ variant = "primary", size = "md", fullWidth = false, className, children, ...rest }: ButtonProps) {
  const classes = cn(
    "relative inline-flex items-center justify-center rounded-md font-semibold transition-colors",
    // Outline, not ring-offset: the offset gap is painted (defaults to
    // white) and reads as a halo on dark surfaces; outline-offset's gap
    // is transparent, so the real surface shows through everywhere.
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-600",
    "disabled:cursor-not-allowed disabled:opacity-70 data-disabled:cursor-not-allowed data-disabled:opacity-70",
    VARIANTS[variant],
    SIZES[size],
    fullWidth && "w-full",
    className,
  );
  if (typeof rest.href === "string") {
    const { href, ...anchor } = rest;
    return (
      <a href={href} {...anchor} className={classes}>
        <TouchTarget>{children}</TouchTarget>
      </a>
    );
  }
  const { loading = false, disabled, ...button } = rest;
  return (
    <HeadlessButton {...button} disabled={Boolean(disabled) || loading} aria-busy={loading || undefined} className={classes}>
      <TouchTarget>{children}</TouchTarget>
    </HeadlessButton>
  );
}
