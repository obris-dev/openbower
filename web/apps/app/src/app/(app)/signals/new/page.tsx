import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { GENERIC_FAILURE, loginUrl, webRoutes } from "@bower/api";

import { CHOOSE_TYPE } from "../_components/copy";
import { CREATE_TITLE } from "../_components/form-parts";
import { SignalHeader } from "../_components/signal-header";
import { TypeChooser } from "../_components/type-chooser";

/** Create signal, first: what the signal collects. The catalog says which
 * hosted types are on offer; one that cannot be read still leaves Custom. */
export default async function NewSignalPage() {
  const cookieHeader = (await cookies()).toString();
  const { fetchSignalsCatalogWithCookie } = await import("@bower/api/server");
  const catalog = await fetchSignalsCatalogWithCookie(cookieHeader);
  if (catalog.status === "unauthenticated") redirect(loginUrl(webRoutes.signalNew));
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">
        <SignalHeader title={CREATE_TITLE}>
          <p className="mt-1 text-sm text-muted">{CHOOSE_TYPE}</p>
        </SignalHeader>
        {catalog.status === "ok" ? (
          <TypeChooser catalog={catalog.data} />
        ) : (
          <TypeChooser catalog={null} failure={catalog.status === "error" ? catalog.message : GENERIC_FAILURE} />
        )}
      </div>
    </div>
  );
}
