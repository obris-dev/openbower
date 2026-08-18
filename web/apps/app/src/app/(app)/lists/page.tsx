import { redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

/** The lists index lives at home; this path survives only so old links
 * land somewhere sensible. Sheet details stay under /lists/[id]. */
export default function ListsPathRedirect() {
  redirect(webRoutes.home);
}
