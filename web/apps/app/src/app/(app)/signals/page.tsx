import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { loginUrl, webRoutes } from "@bower/api";

import { SignalsList } from "./_components/signals-list";

/** The signals a person set up: each fills a sheet with the events it
 * matches, opened from here or from home like any sheet. */
export default async function SignalsPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchLogsPageWithCookie } = await import("@bower/api/server");
  const page = await fetchLogsPageWithCookie(cookieHeader);
  if (page.status === "unauthenticated") redirect(loginUrl(webRoutes.signals));
  if (page.status !== "ok") throw new Error("Signals could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-5xl space-y-6 pb-24 pt-2">
        <SignalsList initialSignals={page.data.items} initialNextCursor={page.data.next_cursor} />
      </div>
    </div>
  );
}
