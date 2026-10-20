"use client";

import { useMemo, useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError, Input, Label } from "@bower/ui";
import {
  createWebhook,
  DESTINATIONS_FULL_CODE,
  probeWebhook,
  WEBHOOK_HEADER_RESERVED_CODE,
  WEBHOOK_LABEL_MAX_LENGTH,
  WEBHOOK_URL_BLOCKED_CODE,
  WEBHOOK_URL_MAX_LENGTH,
  type RenderableProbe,
  type RenderableDestination,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { PROBE_ACTION } from "./copy";
import { HeadersEditor } from "./headers-editor";
import { ProbeOutcome } from "./probe-outcome";
import { contentfulHeaders, headersProblem, urlProblem, type HeaderRow } from "./lib/headers";
import { SecretReveal } from "./secret-reveal";

const FORM_ID = "add-destination-form";
const LABEL_ID = "destination-label";
const LABEL_ERROR_ID = "destination-label-error";
const URL_ID = "destination-url";
const URL_ERROR_ID = "destination-url-error";
const HEADERS_ID = "destination-headers";

// Which gesture last diagnosed the form: a test needs the URL and the
// headers, a save needs the name too, so the name's gap marks only
// after a save.
type Attempt = "test" | "save";

/** The add-destination drawer. Mounted fresh per open, so state resets
 * with the gesture. The footer holds the test beside the add (one
 * unsaved ping to the typed URL, its answer above the buttons, where
 * it never leaves the fold) so a wrong address shows before anything
 * is stored; on save the body swaps to the one-time secret panel and
 * stays until dismissed. */
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
  const [attempted, setAttempted] = useState<Attempt | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<RenderableProbe | null>(null);
  const [urlRefusal, setUrlRefusal] = useState<string | null>(null);
  const [headersRefusal, setHeadersRefusal] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ label: string; secret: string } | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const labelIssue = label.trim() === "" ? "Name this destination." : null;
  const urlIssue = useMemo(() => urlProblem(url), [url]);
  const headersIssue = useMemo(() => headersProblem(headerRows), [headerRows]);
  const sendable = !urlIssue && !headersIssue;
  const ready = !labelIssue && sendable;
  const labelMarked = attempted === "save" && labelIssue !== null;
  const urlMarked = attempted !== null && urlIssue !== null;

  // The actions stay ENABLED and diagnose here: nothing marks before
  // a first attempt, then the first gap is brought into view.
  function blockOn(attempt: Attempt): boolean {
    if (attempt === "save" ? ready : sendable) return false;
    setAttempted(attempt);
    const anchor = attempt === "save" && labelIssue ? LABEL_ID : urlIssue ? URL_ID : HEADERS_ID;
    document.getElementById(anchor)?.scrollIntoView({ block: "nearest" });
    return true;
  }

  // Tier 1: the server's detail renders VERBATIM, at the surface its
  // code names when that surface is knowable; false when it is not.
  function placeRefusal(code: string | undefined, message: string): boolean {
    if (code === WEBHOOK_URL_BLOCKED_CODE) setUrlRefusal(message);
    else if (code === WEBHOOK_HEADER_RESERVED_CODE) setHeadersRefusal(message);
    else return false;
    return true;
  }

  function body() {
    return { label: label.trim(), url: url.trim(), headers: contentfulHeaders(headerRows) };
  }

  async function probe() {
    if (blockOn("test") || probing) return;
    setAttempted(null);
    setProbing(true);
    setProbeError(null);
    setUrlRefusal(null);
    setHeadersRefusal(null);
    const res = await probeWebhook(body());
    setProbing(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      setProbeResult(res.data);
      return;
    }
    if (!placeRefusal(res.code, res.message)) setProbeError(res.message);
  }

  async function submit() {
    if (blockOn("save") || submitting) return;
    setAttempted(null);
    setSubmitting(true);
    setServerError(null);
    setUrlRefusal(null);
    setHeadersRefusal(null);
    const res = await createWebhook(body());
    setSubmitting(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      onCreated(res.data.destination);
      setRevealed({ label: res.data.destination.label, secret: res.data.signing_secret });
      return;
    }
    if (placeRefusal(res.code, res.message)) return;
    if (res.code === DESTINATIONS_FULL_CODE) onFull();
    setServerError(res.message);
  }

  const footer = revealed ? (
    <Button type="button" fullWidth onClick={onClose}>
      Done
    </Button>
  ) : (
    <>
      {serverError && <ErrorMessage message={serverError} />}
      {probeError && <ErrorMessage message={probeError} />}
      {probeResult && <ProbeOutcome probe={probeResult} />}
      <div className="flex gap-2">
        <Button type="button" variant="ghost" loading={probing} onClick={() => void probe()}>
          {PROBE_ACTION}
        </Button>
        <Button type="submit" form={FORM_ID} loading={submitting} className="flex-1">
          Add destination
        </Button>
      </div>
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
              warned={labelMarked}
              aria-describedby={labelMarked ? LABEL_ERROR_ID : undefined}
              autoFocus
              className="mt-1"
            />
            {labelMarked && (
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
                // An answer belongs to the address it was sent to.
                setProbeResult(null);
              }}
              placeholder="https://hooks.example.com/openbower"
              maxLength={WEBHOOK_URL_MAX_LENGTH}
              warned={urlMarked}
              invalid={urlRefusal !== null}
              aria-describedby={urlMarked || urlRefusal ? URL_ERROR_ID : undefined}
              className="mt-1"
            />
            {urlMarked && (
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
              setProbeResult(null);
            }}
            problem={attempted !== null ? headersIssue : null}
            refusal={headersRefusal}
          />
        </form>
      )}
    </Drawer>
  );
}
