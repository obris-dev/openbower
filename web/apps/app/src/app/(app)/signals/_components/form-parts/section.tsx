import type { ReactNode } from "react";

/** One part of the setup, titled as the signal works. A title naming a
 * single field labels it. */
export function Section({
  title,
  aside,
  titleFor,
  children,
}: {
  title: string;
  aside?: string;
  titleFor?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4 p-6">
      <h2 className="flex items-baseline gap-2 text-base font-semibold text-foreground">
        {titleFor ? <label htmlFor={titleFor}>{title}</label> : title}
        {aside && <span className="text-xs font-normal text-muted">{aside}</span>}
      </h2>
      {children}
    </section>
  );
}
