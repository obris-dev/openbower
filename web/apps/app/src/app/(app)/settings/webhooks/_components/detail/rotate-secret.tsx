"use client";

import { useState } from "react";
import { Button, useToast } from "@bower/ui";
import { rotateWebhook, type RenderableDestination } from "@bower/api";

import { ensureOk } from "@/lib/ensure-ok";
import { ConfirmDelete } from "../../../../_components/confirm-delete";
import { DONE, ROTATE_CONSEQUENCE, ROTATE_QUESTION, ROTATE_SECRET, SECRET_ROTATED_NOTE, rotatedLede } from "../copy";
import { SecretReveal } from "../secret-reveal";

/** Rotate the signing secret: a ghost verb, the confirm ritual (it
 * invalidates the old secret after the grace window), then the new
 * secret shown once through the same panel a create uses. */
export function RotateSecret({
  destinationId,
  label,
  onRotated,
}: {
  destinationId: string;
  label: string;
  onRotated: (destination: RenderableDestination) => void;
}) {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [revealed, setRevealed] = useState<string | null>(null);

  async function rotate() {
    setRotating(true);
    const res = await rotateWebhook(destinationId);
    setRotating(false);
    if (!ensureOk(res, toast, { title: "Secret not rotated" })) return;
    setConfirming(false);
    setRevealed(res.data.signing_secret);
    onRotated(res.data.destination);
  }

  if (revealed !== null) {
    return (
      <div className="space-y-3">
        <SecretReveal label={label} secret={revealed} lede={rotatedLede(label)} note={SECRET_ROTATED_NOTE} guide={false} />
        <Button type="button" variant="ghost" size="sm" onClick={() => setRevealed(null)}>
          {DONE}
        </Button>
      </div>
    );
  }
  if (confirming) {
    return (
      <ConfirmDelete
        verb="Rotate"
        question={ROTATE_QUESTION}
        consequence={ROTATE_CONSEQUENCE}
        busy={rotating}
        onCancel={() => setConfirming(false)}
        onDelete={() => void rotate()}
      />
    );
  }
  return (
    <Button type="button" variant="ghost" size="sm" onClick={() => setConfirming(true)}>
      {ROTATE_SECRET}
    </Button>
  );
}
