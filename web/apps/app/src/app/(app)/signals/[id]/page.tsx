import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { SIGNAL_FEED, loginUrl, signalFeed, webRoutes, type SignalsCatalog } from "@bower/api";

import { SignalSettings } from "../_components/signal-settings";

/** A signal's settings: the signal with its listener read live, then the
 * catalog its form needs, read for a hosted signal only. */
export default async function SignalSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieHeader = (await cookies()).toString();
  const { fetchSignalWithCookie, fetchSignalsCatalogWithCookie } = await import("@bower/api/server");
  const log = await fetchSignalWithCookie(cookieHeader, id);
  if (log.status === "missing") notFound();
  if (log.status === "unauthenticated") redirect(loginUrl(webRoutes.signal(id)));
  if (log.status !== "ok") throw new Error("The signal could not be loaded.");
  let catalog: SignalsCatalog | null = null;
  if (signalFeed(log.data) === SIGNAL_FEED.HOSTED) {
    const read = await fetchSignalsCatalogWithCookie(cookieHeader);
    if (read.status === "unauthenticated") redirect(loginUrl(webRoutes.signal(id)));
    if (read.status !== "ok") throw new Error("The signals catalog could not be loaded.");
    catalog = read.data;
  }
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">
        <SignalSettings log={log.data} catalog={catalog} />
      </div>
    </div>
  );
}
