import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

import { Builder } from "../_components/builder";

export default async function EditAgentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieHeader = (await cookies()).toString();
  const { fetchAgentWithCookie } = await import("@bower/api/server");
  const agent = await fetchAgentWithCookie(cookieHeader, id);
  if (agent.status === "missing") notFound();
  if (agent.status === "unauthenticated") redirect(webRoutes.login);
  if (agent.status !== "ok") throw new Error("The agent could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">
        <Builder agent={agent.data} />
      </div>
    </div>
  );
}
