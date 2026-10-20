import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { loginUrl, webRoutes } from "@bower/api";

import { Roster } from "./_components";

/** The machine API keys: what a script adds rows with, minted here
 * and shown once. */
export default async function ApiKeysSettingsPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchApiKeysWithCookie } = await import("@bower/api/server");
  const keys = await fetchApiKeysWithCookie(cookieHeader);
  if (keys.status === "unauthenticated") redirect(loginUrl(webRoutes.settingsApiKeys));
  if (keys.status !== "ok") throw new Error("API keys could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6 pb-24 pt-2">
        <Roster initialKeys={keys.data.keys} />
      </div>
    </div>
  );
}
