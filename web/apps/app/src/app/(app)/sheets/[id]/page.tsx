import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { loginUrl, webRoutes, ROWS_FIRST_PAGE, SHEET_KIND } from "@bower/api";

import { periodFrom, Sheet, sinceFor, type Period } from "../_components/sheet";

/** Where a log's period starts as this request is served; none on a
 * standard sheet. */
function periodStart(period: Period | null): string | undefined {
  return period === null ? undefined : sinceFor(period, Date.now());
}

export default async function SheetDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const cookieHeader = (await cookies()).toString();
  const { fetchLogWithCookie, fetchSheetRowsWithCookie, fetchSheetWithCookie } = await import("@bower/api/server");
  // The detail comes first: a log's rows read one period, which only its
  // kind says applies.
  const detail = await fetchSheetWithCookie(cookieHeader, id);
  // Each outcome renders as itself: only a real 404 is notFound; a down
  // backend is the error boundary, never a lying "not found".
  if (detail.status === "missing") notFound();
  if (detail.status === "unauthenticated") redirect(loginUrl(webRoutes.sheet(id)));
  if (detail.status !== "ok") throw new Error("The sheet could not be loaded.");
  const isLog = detail.data.kind === SHEET_KIND.LOG;
  const period = isLog ? periodFrom(query.period) : null;
  const [rows, log] = await Promise.all([
    fetchSheetRowsWithCookie(cookieHeader, id, ROWS_FIRST_PAGE, { since: periodStart(period) }),
    isLog ? fetchLogWithCookie(cookieHeader, id) : null,
  ]);
  // The rows leg gets the same honesty as the detail leg: a fetch
  // failure must not render a zero-row sheet that looks real.
  if (rows.status === "missing") notFound();
  if (rows.status === "unauthenticated") redirect(loginUrl(webRoutes.sheet(id)));
  if (rows.status !== "ok") throw new Error("The sheet's rows could not be loaded.");
  // A signal's own state is read live from the signals service; when that
  // read fails the sheet still renders, with no status or banner.
  const initialLog = log?.status === "ok" ? log.data : null;
  // Full-bleed: the sheet owns the shell's whole content viewport in
  // the spreadsheet idiom (its own toolbar and status bands, the grid
  // as the one scroll region), so this route adds no page container.
  return (
    // The HARD height (the shell's main is min-height only): the
    // grid region inside the Sheet owns the ONLY scroll, so the page
    // itself never scrolls. The 4rem mirrors @bower/ui sidebar.tsx's
    // main calc (its top bar height); change them together.
    <div className="flex h-[calc(100dvh-4rem)] flex-col overflow-hidden">
      {/* Keyed on the period: a new one starts the rows, their cursor,
          the selection and the scroll over together. */}
      <Sheet
        key={period ?? id}
        initialDetail={detail.data}
        initialRows={rows.data}
        period={period}
        initialLog={initialLog}
      />
    </div>
  );
}
