import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { loginUrl, webRoutes } from "@bower/api";

import { SignalSettings } from "../_components/signal-settings";

/** A signal's settings: the signal with its listener read live, and the
 * catalog its form needs, read together. */
export default async function SignalSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieHeader = (await cookies()).toString();
  const { fetchLogWithCookie, fetchSignalsCatalogWithCookie } = await import("@bower/api/server");
  const [log, catalog] = await Promise.all([
    fetchLogWithCookie(cookieHeader, id),
    fetchSignalsCatalogWithCookie(cookieHeader),
  ]);
  if (log.status === "missing") notFound();
  if (log.status === "unauthenticated" || catalog.status === "unauthenticated") redirect(loginUrl(webRoutes.signal(id)));
  if (log.status !== "ok") throw new Error("The signal could not be loaded.");
  if (catalog.status !== "ok") throw new Error("The signals catalog could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">
        <SignalSettings log={log.data} catalog={catalog.data} />
      </div>
    </div>
  );
}
