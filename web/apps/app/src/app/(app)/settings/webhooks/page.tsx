import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

import { Roster } from "./_components";

/** The webhook destinations: where signed deliveries go, configured
 * once for the account and reused by every sheet. */
export default async function WebhooksSettingsPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchWebhooksWithCookie } = await import("@bower/api/server");
  const destinations = await fetchWebhooksWithCookie(cookieHeader);
  if (destinations.status === "unauthenticated") redirect(webRoutes.login);
  if (destinations.status !== "ok") throw new Error("Webhook destinations could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6 pb-24 pt-2">
        <Roster initialDestinations={destinations.data.items} />
      </div>
    </div>
  );
}
