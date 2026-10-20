"use client";

import { useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError, Input, Label } from "@bower/ui";
import { API_KEY_NAME_MAX_LENGTH, mintApiKey, type ApiKeyWire } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { SecretField } from "../../../_components/secret-field";
import { CREATE_ACTION, TOKEN_NOTE } from "./copy";

const FORM_ID = "create-key-form";
const NAME_ID = "api-key-name";
const NAME_ERROR_ID = "api-key-name-error";
const TOKEN_ID = "api-key-token";

/** The create drawer. Mounted fresh per open, so state resets with the
 * gesture; on success the body swaps to the one-time token and stays
 * until dismissed. */
export function CreateKeyDrawer(props: { open: boolean; onClose: () => void; onCreated: (key: ApiKeyWire) => void }) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
      onCreated={props.onCreated}
    />
  );
}

function DrawerContent({
  open,
  afterLeave,
  onClose,
  onCreated,
}: {
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
  onCreated: (key: ApiKeyWire) => void;
}) {
  const [name, setName] = useState("");
  const [attempted, setAttempted] = useState(false);
  const [creating, setCreating] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const nameIssue = name.trim() === "" ? "Name this key for the script that will use it." : null;

  // A close while the request is in flight would unmount the only
  // place the token can ever be shown, leaving a live key nobody can
  // use; the drawer stays until the answer lands.
  function close() {
    if (creating) return;
    onClose();
  }

  async function submit() {
    if (creating) return;
    if (nameIssue) {
      setAttempted(true);
      return;
    }
    setCreating(true);
    setServerError(null);
    const res = await mintApiKey(name.trim());
    setCreating(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      onCreated(res.data.key);
      setCreated(res.data.token);
      return;
    }
    // Tier 1: the identity service's detail renders verbatim (a bad
    // name, keys disabled on this deployment, the per-user limit).
    setServerError(res.message);
  }

  const footer = created ? (
    <Button type="button" fullWidth onClick={onClose}>
      Close
    </Button>
  ) : (
    <>
      {serverError && <ErrorMessage message={serverError} />}
      <Button type="submit" form={FORM_ID} fullWidth loading={creating}>
        {CREATE_ACTION}
      </Button>
    </>
  );

  return (
    <Drawer open={open} onClose={close} title={CREATE_ACTION} afterLeave={afterLeave} footer={footer}>
      {created ? (
        <div className="space-y-6">
          <SecretField id={TOKEN_ID} label="API key" value={created} note={TOKEN_NOTE} />
        </div>
      ) : (
        <form
          id={FORM_ID}
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Label htmlFor={NAME_ID}>Name</Label>
          <Input
            id={NAME_ID}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Import script"
            maxLength={API_KEY_NAME_MAX_LENGTH}
            warned={attempted && nameIssue !== null}
            aria-describedby={attempted && nameIssue ? NAME_ERROR_ID : undefined}
            autoFocus
            className="mt-1"
          />
          {attempted && nameIssue && (
            <FieldError id={NAME_ERROR_ID} tone="warning">
              {nameIssue}
            </FieldError>
          )}
        </form>
      )}
    </Drawer>
  );
}
