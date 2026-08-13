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
  // Paper on signal-600: the vivid end of what can carry light text.
  // 3.4:1 clears WCAG's 3:1 tier (large text / UI components) but not
  // the strict 4.5:1 for small text: a DELIBERATE trade of one
  // compliance notch for the brand pop (the bright mark teal itself is
  // 2.4:1 and never carries text; the 700 step passes 4.9:1 but reads
  // somber). Disabled relies on the shared opacity dim.
  primary: "bg-signal-600 text-paper hover:bg-signal-700",
  secondary: "bg-wash text-foreground hover:bg-wash-strong",
  outline: "border border-edge text-foreground hover:bg-wash",
  ghost: "text-foreground hover:bg-wash",
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
