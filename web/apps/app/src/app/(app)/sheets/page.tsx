import { redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

/** The sheets index lives at home, so a link to /sheets lands there.
 * Sheet details live under /sheets/[id]. */
export default function SheetsPathRedirect() {
  redirect(webRoutes.home);
}
