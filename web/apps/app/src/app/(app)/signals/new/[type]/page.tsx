import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CUSTOM_SIGNAL_TYPE, loginUrl, webRoutes } from "@bower/api";

import { CustomSignalForm } from "../../_components/custom-signal-form";
import { SignalForm } from "../../_components/signal-form";

/** Create a signal of one type. Custom needs nothing from the signals
 * service; a hosted type's form arrives with the catalog, so its limits
 * and default threshold are there on first paint, and a type the catalog
 * does not offer has no page. */
export default async function NewSignalOfTypePage({ params }: { params: Promise<{ type: string }> }) {
  const { type } = await params;
  const body =
    type === CUSTOM_SIGNAL_TYPE ? (
      <CustomSignalForm />
    ) : (
      await hostedForm(type)
    );
  return (
    <div className="p-6">
      <div className="mx-auto w-full max-w-6xl space-y-6 pb-28 pt-2">{body}</div>
    </div>
  );
}

async function hostedForm(type: string) {
  const cookieHeader = (await cookies()).toString();
  const { fetchSignalsCatalogWithCookie } = await import("@bower/api/server");
  const catalog = await fetchSignalsCatalogWithCookie(cookieHeader);
  if (catalog.status === "unauthenticated") redirect(loginUrl(webRoutes.signalNewOf(type)));
  if (catalog.status !== "ok") throw new Error("The signals catalog could not be loaded.");
  const offered = catalog.data.types.find((each) => each.type === type);
  if (offered === undefined) notFound();
  return <SignalForm catalog={{ ...catalog.data, types: [offered] }} />;
}
