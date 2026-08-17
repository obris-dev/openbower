import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { webRoutes } from "@bower/api";

import { COLLAPSED_COOKIE, Directory } from "./_components/directory";

/** Home IS the directory of sheets: they are the unit of work, so the
 * workspace opens on them (Discover is a tool that feeds sheets, not
 * the center). Sheet details live under /lists/[id]. */
export default async function Home() {
  const jar = await cookies();
  const cookieHeader = jar.toString();
  const collapsed = (jar.get(COLLAPSED_COOKIE)?.value ?? "").split(".").filter(Boolean);
  const { fetchFoldersWithCookie, fetchListsPageWithCookie } = await import("@bower/api/server");
  const [listsRes, foldersRes] = await Promise.all([
    fetchListsPageWithCookie(cookieHeader),
    fetchFoldersWithCookie(cookieHeader),
  ]);
  if (listsRes.status === "unauthenticated" || foldersRes.status === "unauthenticated") redirect(webRoutes.login);
  // The two legs fail separately: no lists is a full failure surface,
  // no folders means lists still render (flattened loose) UNDER a
  // banner saying so, never silently.
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-5xl space-y-6 pb-24 pt-2">
        <Directory
          initialLists={listsRes.status === "ok" ? listsRes.data : null}
          initialFolders={foldersRes.status === "ok" ? foldersRes.data.items : []}
          initialCollapsed={collapsed}
          initialListsFailed={listsRes.status !== "ok"}
          initialFoldersFailed={foldersRes.status !== "ok"}
        />
      </div>
    </div>
  );
}
