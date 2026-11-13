import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { EmptyState } from "@bower/ui";

import { loginUrl, webRoutes } from "@bower/api";

import { SignalForm } from "./_components/signal-form";

const NONE_OFFERED_TITLE = "No signals to follow";
const NONE_OFFERED_SUBTITLE = "This version of the app knows none of the signals the service offers. Update it to create one.";

/** Create a signal: the catalog arrives with the page, so the form's
 * limits and default threshold are there on first paint. */
export default async function NewSignalPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchSignalsCatalogWithCookie } = await import("@bower/api/server");
  const catalog = await fetchSignalsCatalogWithCookie(cookieHeader);
  if (catalog.status === "unauthenticated") redirect(loginUrl(webRoutes.signalNew));
  if (catalog.status !== "ok") throw new Error("The signals catalog could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">
        {catalog.data.signals.length === 0 ? (
          <EmptyState title={NONE_OFFERED_TITLE} subtitle={NONE_OFFERED_SUBTITLE} />
        ) : (
          <SignalForm catalog={catalog.data} />
        )}
      </div>
    </div>
  );
}
