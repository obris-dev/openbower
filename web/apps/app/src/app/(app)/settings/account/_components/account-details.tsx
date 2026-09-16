"use client";

import { Card, CopyButton } from "@bower/ui";
import { webRoutes } from "@bower/api";
import { useUser } from "@bower/auth";

import { Breadcrumbs } from "../../../_components/breadcrumbs";

/** Email and account id off the seeded session (the shell resolved the
 * user server-side, so this renders without a fetch). The id is what
 * support asks for; the copy button is its one action. */
export function AccountDetails() {
  const { user } = useUser();
  return (
    <>
      <div className="space-y-2">
        <Breadcrumbs trail={[{ label: "Settings", href: webRoutes.settings }]} />
        <h1 className="text-2xl font-bold text-foreground">Account</h1>
      </div>
      <Card className="divide-y divide-hairline p-0">
        <div className="px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-faint">Email</p>
          <p className="mt-1 text-sm text-foreground">{user?.email ?? "…"}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3 px-5 py-4">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-faint">Account id</p>
            <code className="mt-1 block truncate font-mono text-sm text-foreground">{user?.account_id ?? "…"}</code>
          </div>
          {user && <CopyButton value={user.account_id} />}
        </div>
      </Card>
    </>
  );
}
