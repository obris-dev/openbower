import type { ReactNode } from "react";
import { Card } from "./card";

/** The centered say-what-happened card: empty workspaces, failures,
 * error boundaries. Actions ride as children. */
export function EmptyState({ title, subtitle, children }: { title: string; subtitle?: string; children?: ReactNode }) {
  return (
    <Card className="p-10 text-center">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {subtitle && <p className="mx-auto mt-1 max-w-md text-sm text-muted">{subtitle}</p>}
      {children && <div className="mt-4 flex justify-center gap-2">{children}</div>}
    </Card>
  );
}
