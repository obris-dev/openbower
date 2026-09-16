"use client";

import { useMemo, useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError, Input, Label } from "@bower/ui";
import {
  createWebhook,
  DESTINATIONS_FULL_CODE,
  WEBHOOK_HEADER_RESERVED_CODE,
  WEBHOOK_LABEL_MAX_LENGTH,
  WEBHOOK_URL_BLOCKED_CODE,
  WEBHOOK_URL_MAX_LENGTH,
  type RenderableDestination,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { HeadersEditor } from "./headers-editor";
import { contentfulHeaders, headersProblem, urlProblem, type HeaderRow } from "./lib/headers";
import { SecretReveal } from "./secret-reveal";

const FORM_ID = "add-destination-form";
const LABEL_ID = "destination-label";
const LABEL_ERROR_ID = "destination-label-error";
const URL_ID = "destination-url";
const URL_ERROR_ID = "destination-url-error";
const HEADERS_ID = "destination-headers";

/** The add-destination drawer. Mounted fresh per open, so state resets
 * with the gesture; on success the body swaps to the one-time secret
 * panel and stays until dismissed. */
export function AddDestinationDrawer(props: {
  open: boolean;
  onClose: () => void;
  onCreated: (destination: RenderableDestination) => void;
  onFull: () => void;
}) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
      onCreated={props.onCreated}
      onFull={props.onFull}
    />
  );
}

function DrawerContent({
  open,
  afterLeave,
  onClose,
  onCreated,
  onFull,
}: {
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
  onCreated: (destination: RenderableDestination) => void;
  onFull: () => void;
}) {
  const [label, setLabel] = useState("");
  const [url, setUrl] = useState("");
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [urlRefusal, setUrlRefusal] = useState<string | null>(null);
  const [headersRefusal, setHeadersRefusal] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ label: string; secret: string } | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const labelIssue = label.trim() === "" ? "Name this destination." : null;
  const urlIssue = useMemo(() => urlProblem(url), [url]);
  const headersIssue = useMemo(() => headersProblem(headerRows), [headerRows]);
  const ready = !labelIssue && !urlIssue && !headersIssue;

  // The action stays ENABLED and diagnoses here: nothing marks before
  // a first attempt, then the first gap is brought into view.
  function blockOn(): boolean {
    if (ready) return false;
    setAttempted(true);
    const anchor = labelIssue ? LABEL_ID : urlIssue ? URL_ID : HEADERS_ID;
    document.getElementById(anchor)?.scrollIntoView({ block: "nearest" });
    return true;
  }

  async function submit() {
    if (blockOn() || submitting) return;
    setAttempted(false);
    setSubmitting(true);
    setServerError(null);
    setUrlRefusal(null);
    setHeadersRefusal(null);
    const res = await createWebhook({ label: label.trim(), url: url.trim(), headers: contentfulHeaders(headerRows) });
    setSubmitting(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      onCreated(res.data.destination);
      setRevealed({ label: res.data.destination.label, secret: res.data.signing_secret });
      return;
    }
    // Tier 1: the server's detail renders VERBATIM, at the surface its
    // code names when that surface is knowable.
    if (res.code === WEBHOOK_URL_BLOCKED_CODE) setUrlRefusal(res.message);
    else if (res.code === WEBHOOK_HEADER_RESERVED_CODE) setHeadersRefusal(res.message);
    else {
      if (res.code === DESTINATIONS_FULL_CODE) onFull();
      setServerError(res.message);
    }
  }

  const footer = revealed ? (
    <Button type="button" fullWidth onClick={onClose}>
      Done
    </Button>
  ) : (
    <>
      {serverError && <ErrorMessage message={serverError} />}
      <Button type="submit" form={FORM_ID} fullWidth loading={submitting}>
        Add destination
      </Button>
    </>
  );

  return (
    <Drawer open={open} onClose={onClose} title="Add destination" width="lg" afterLeave={afterLeave} footer={footer}>
      {revealed ? (
        <SecretReveal label={revealed.label} secret={revealed.secret} />
      ) : (
        <form
          id={FORM_ID}
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
          className="space-y-5"
        >
          <div>
            <Label htmlFor={LABEL_ID}>Name</Label>
            <Input
              id={LABEL_ID}
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="CRM sync"
              maxLength={WEBHOOK_LABEL_MAX_LENGTH}
              warned={attempted && labelIssue !== null}
              aria-describedby={attempted && labelIssue ? LABEL_ERROR_ID : undefined}
              autoFocus
              className="mt-1"
            />
            {attempted && labelIssue && (
              <FieldError id={LABEL_ERROR_ID} tone="warning">
                {labelIssue}
              </FieldError>
            )}
          </div>
          <div>
            <Label htmlFor={URL_ID}>URL</Label>
            <Input
              id={URL_ID}
              type="text"
              inputMode="url"
              value={url}
              onChange={(event) => {
                setUrl(event.target.value);
                setUrlRefusal(null);
              }}
              placeholder="https://hooks.example.com/openbower"
              maxLength={WEBHOOK_URL_MAX_LENGTH}
              warned={attempted && urlIssue !== null}
              invalid={urlRefusal !== null}
              aria-describedby={(attempted && urlIssue) || urlRefusal ? URL_ERROR_ID : undefined}
              className="mt-1"
            />
            {attempted && urlIssue && (
              <FieldError id={URL_ERROR_ID} tone="warning">
                {urlIssue}
              </FieldError>
            )}
            {urlRefusal && <FieldError id={URL_ERROR_ID}>{urlRefusal}</FieldError>}
          </div>
          <HeadersEditor
            id={HEADERS_ID}
            rows={headerRows}
            onChange={(rows) => {
              setHeaderRows(rows);
              setHeadersRefusal(null);
            }}
            problem={attempted ? headersIssue : null}
            refusal={headersRefusal}
          />
        </form>
      )}
    </Drawer>
  );
}
