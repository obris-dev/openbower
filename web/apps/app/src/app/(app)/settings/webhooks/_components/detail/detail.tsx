"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, useToast } from "@bower/ui";
import { DESTINATION_IN_USE_CODE, deleteWebhook, webRoutes, type RenderableDestination } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { Breadcrumbs } from "../../../../_components/breadcrumbs";
import { SECRET_RECOVERY_NOTE, rotationLine, usageLine } from "../copy";
import { DeliveryLog } from "../delivery-log";
import { hostOf } from "../../../../_components/webhook-delivery";
import { useDeliveries } from "../use-deliveries";
import { VerifyGuide } from "../verify-guide";
import { DetailFooter, type DeleteOutcome } from "./footer";
import { RotateSecret } from "./rotate-secret";
import { SettingsForm } from "./settings-form";
import { TestPanel } from "./test-panel";
import { useDestinationForm } from "./use-destination-form";

/** One destination: the orchestrator holds the saved destination and
 * composes the settings form, the test panel, the verify guide with
 * the rotate action, the delivery log, and the footer. The secret is
 * not here: it was shown once at create, and shows once again on a
 * rotate. */
export function Detail({ initialDestination }: { initialDestination: RenderableDestination }) {
  const router = useRouter();
  const toast = useToast();
  const [destination, setDestination] = useState(initialDestination);
  const form = useDestinationForm(destination, setDestination);
  const deliveries = useDeliveries(destination.id);
  const usage = usageLine(destination.column_count);

  // A refusal in use (a webhook column sends here) comes BACK to the
  // confirm tier in the server's words; any other failure toasts.
  async function remove(): Promise<DeleteOutcome> {
    const res = await deleteWebhook(destination.id);
    if (redirectIfUnauthenticated(res)) return { status: "failed" };
    if (res.status !== "ok") {
      if (res.code === DESTINATION_IN_USE_CODE) return { status: "refused", detail: res.message };
      toast.error(res.message, "Destination not deleted");
      return { status: "failed" };
    }
    toast.success("Destination deleted.", destination.label);
    router.push(webRoutes.settingsWebhooks);
    return { status: "deleted" };
  }

  return (
    <>
      <div className="space-y-2">
        <Breadcrumbs
          trail={[
            { label: "Settings", href: webRoutes.settings },
            { label: "Webhooks", href: webRoutes.settingsWebhooks },
          ]}
        />
        <h1 className="truncate text-2xl font-bold text-foreground">{destination.label}</h1>
        <p className="text-sm text-muted">{hostOf(destination.url)}</p>
        {usage && <p className="text-xs text-faint">{usage}</p>}
      </div>

      <Card className="p-6">
        <SettingsForm form={form} headerNames={destination.header_names} />
      </Card>

      <Card className="space-y-4 p-6">
        <TestPanel
          destinationId={destination.id}
          onDelivered={(delivery) => {
            deliveries.prepend(delivery);
            setDestination((prev) => ({ ...prev, last_delivery: delivery }));
          }}
        />
      </Card>

      <Card className="space-y-3 p-6">
        <details className="group">
          <summary className="cursor-pointer text-sm font-semibold text-foreground">Verifying deliveries</summary>
          <div className="mt-3">
            <VerifyGuide heading={false} />
          </div>
        </details>
        <p className="text-xs text-faint">{SECRET_RECOVERY_NOTE}</p>
        {destination.rotated_at && <p className="text-xs text-faint">{rotationLine(destination.rotated_at)}</p>}
        <RotateSecret
          destinationId={destination.id}
          label={destination.label}
          onRotated={(rotated) => setDestination((prev) => ({ ...prev, rotated_at: rotated.rotated_at }))}
        />
      </Card>

      <Card className="space-y-4 p-6">
        <h2 className="text-sm font-semibold text-foreground">Deliveries</h2>
        <DeliveryLog
          items={deliveries.items}
          failed={deliveries.failed}
          hasMore={deliveries.hasMore}
          loadingMore={deliveries.loadingMore}
          onRetry={deliveries.retry}
          onLoadMore={deliveries.loadMore}
        />
      </Card>

      <DetailFooter saving={form.saving} serverError={form.serverError} onDelete={remove} />
    </>
  );
}
