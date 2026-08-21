import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

import { Roster } from "./_components/roster";

/** The agents roster: named research configurations kept, tested, and
 * pointed at sheets. */
export default async function AgentsPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchAgentsWithCookie } = await import("@bower/api/server");
  const agents = await fetchAgentsWithCookie(cookieHeader);
  if (agents.status === "unauthenticated") redirect(webRoutes.login);
  if (agents.status !== "ok") throw new Error("Agents could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-5xl space-y-6 pb-24 pt-2">
        <Roster initialAgents={agents.data.items} />
      </div>
    </div>
  );
}
