import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { ROWS_FIRST_PAGE, webRoutes } from "@bower/api";

import { Sheet } from "../_components/sheet";

export default async function ListDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const cookieHeader = (await cookies()).toString();
  const { fetchListRowsWithCookie, fetchListWithCookie } = await import("@bower/api/server");
  const [detail, rows] = await Promise.all([
    fetchListWithCookie(cookieHeader, id),
    fetchListRowsWithCookie(cookieHeader, id, ROWS_FIRST_PAGE),
  ]);
  // Each outcome renders as itself: only a real 404 is notFound; a down
  // backend is the error boundary, never a lying "not found".
  if (detail.status === "missing") notFound();
  if (detail.status === "unauthenticated") redirect(webRoutes.login);
  if (detail.status !== "ok") throw new Error("The sheet could not be loaded.");
  // The rows leg gets the same honesty as the detail leg: a fetch
  // failure must not render a zero-row sheet that looks real.
  if (rows.status === "missing") notFound();
  if (rows.status === "unauthenticated") redirect(webRoutes.login);
  if (rows.status !== "ok") throw new Error("The sheet's rows could not be loaded.");
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-7xl space-y-6 pb-24 pt-2">
        <Sheet initialDetail={detail.data} initialRows={rows.data} />
      </div>
    </div>
  );
}
