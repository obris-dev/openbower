import Link from "next/link";
import type { ComponentProps } from "react";
import { buttonClasses, TouchTarget } from "@bower/ui";

/** A next/link in Button's coat, for IN-APP navigation that looks
 * like an action. Button's own `href` form renders a bare anchor (a
 * full document load), which is for boundary crossings only (login,
 * logout, OAuth); everything inside the app routes through here.
 * Lives ABOVE the route groups: the root not-found page needs it
 * too, and a group's _components are that group's own. Open props:
 * everything Link takes rides through. */
export function LinkButton({
  variant = "primary",
  size = "md",
  className,
  children,
  ...rest
}: {
  variant?: Parameters<typeof buttonClasses>[0]["variant"];
  size?: Parameters<typeof buttonClasses>[0]["size"];
  className?: string;
} & Omit<ComponentProps<typeof Link>, "className">) {
  return (
    <Link {...rest} className={buttonClasses({ variant, size }, className)}>
      <TouchTarget>{children}</TouchTarget>
    </Link>
  );
}
