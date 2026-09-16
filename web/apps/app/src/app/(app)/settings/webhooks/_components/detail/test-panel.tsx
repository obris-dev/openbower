"use client";

import { useState } from "react";
import { Send } from "lucide-react";
import { Button, useToast } from "@bower/ui";
import { testWebhook, type RenderableDelivery } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { DeliveryResult } from "../../../../_components/webhook-delivery";

/** Send one test ping and show what came back, inline. The request
 * itself failing is toast tier (nothing on the page to mark); the
 * delivery's own outcome renders here whatever it was, and the parent
 * hears about it to update the log and the health line. */
export function TestPanel({
  destinationId,
  onDelivered,
}: {
  destinationId: string;
  onDelivered: (delivery: RenderableDelivery) => void;
}) {
  const toast = useToast();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<RenderableDelivery | null>(null);

  async function sendTest() {
    if (testing) return;
    setTesting(true);
    const res = await testWebhook(destinationId);
    setTesting(false);
    if (!ensureOk(res, toast, { title: "Test not sent" })) return;
    setResult(res.data);
    onDelivered(res.data);
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Test delivery</h2>
          <p className="text-xs text-muted">Sends one signed ping now and shows what the receiver answered.</p>
        </div>
        <Button type="button" variant="secondary" loading={testing} onClick={() => void sendTest()}>
          <Send aria-hidden className="mr-1.5 h-4 w-4" />
          Send test delivery
        </Button>
      </div>
      {result && <DeliveryResult delivery={result} />}
    </>
  );
}
