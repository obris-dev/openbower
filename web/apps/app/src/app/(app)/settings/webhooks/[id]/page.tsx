import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

import { Detail } from "../_components";

export default async function WebhookDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieHeader = (await cookies()).toString();
  const { fetchWebhookWithCookie } = await import("@bower/api/server");
  const destination = await fetchWebhookWithCookie(cookieHeader, id);
  if (destination.status === "missing") notFound();
  if (destination.status === "unauthenticated") redirect(webRoutes.login);
  if (destination.status !== "ok") throw new Error("The destination could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-3xl space-y-6 pb-28 pt-2">
        <Detail initialDestination={destination.data} />
      </div>
    </div>
  );
}
