"use client";

import type { AnchorHTMLAttributes, ReactNode } from "react";
import { Button as HeadlessButton, type ButtonProps as HeadlessButtonProps } from "@headlessui/react";
import { buttonClasses, TouchTarget, type ButtonSize, type ButtonVariant } from "./button-classes";

type Variant = ButtonVariant;
type Size = ButtonSize;

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

export function Button({ variant = "primary", size = "md", fullWidth = false, className, children, ...rest }: ButtonProps) {
  const classes = buttonClasses({ variant, size, fullWidth }, className);
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
