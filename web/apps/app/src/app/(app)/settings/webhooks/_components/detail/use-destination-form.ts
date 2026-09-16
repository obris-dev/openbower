"use client";

import { useMemo, useState } from "react";
import { useToast } from "@bower/ui";
import {
  updateWebhook,
  WEBHOOK_HEADER_RESERVED_CODE,
  WEBHOOK_URL_BLOCKED_CODE,
  type DestinationPatchBody,
  type RenderableDestination,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { contentfulHeaders, headersProblem, urlProblem, type HeaderProblem, type HeaderRow } from "../lib/headers";

export const FORM_ID = "destination-form";
export const LABEL_ID = "destination-label";
export const URL_ID = "destination-url";
export const HEADERS_ID = "destination-headers";

export type DestinationForm = {
  label: string;
  url: string;
  enabled: boolean;
  replacingHeaders: boolean;
  headerRows: HeaderRow[];
  setLabel: (value: string) => void;
  setUrl: (value: string) => void;
  setEnabled: (value: boolean) => void;
  startReplacingHeaders: () => void;
  stopReplacingHeaders: () => void;
  setHeaderRows: (rows: HeaderRow[]) => void;
  attempted: boolean;
  labelIssue: string | null;
  urlIssue: string | null;
  headersIssue: HeaderProblem | null;
  urlRefusal: string | null;
  headersRefusal: string | null;
  serverError: string | null;
  saving: boolean;
  dirty: boolean;
  save: () => Promise<void>;
};

/** The settings form's state and its save: what changed against the
 * saved destination becomes the PATCH, gaps mark only after a first
 * attempt, and a refusal lands on the field its code names. Headers
 * are replaced as a set, since values are never readable: while the
 * editor is open the patch carries its rows (an empty set clears the
 * headers, which the form says out loud), and Cancel folds it back
 * with nothing carried. */
export function useDestinationForm(
  destination: RenderableDestination,
  onSaved: (saved: RenderableDestination) => void,
): DestinationForm {
  const toast = useToast();
  const [label, setLabel] = useState(destination.label);
  const [url, setUrlState] = useState(destination.url);
  const [enabled, setEnabled] = useState(destination.enabled);
  const [replacingHeaders, setReplacingHeaders] = useState(false);
  const [headerRows, setHeaderRowsState] = useState<HeaderRow[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [urlRefusal, setUrlRefusal] = useState<string | null>(null);
  const [headersRefusal, setHeadersRefusal] = useState<string | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const labelIssue = label.trim() === "" ? "Name this destination." : null;
  const urlIssue = useMemo(() => urlProblem(url), [url]);
  const headersIssue = useMemo(() => (replacingHeaders ? headersProblem(headerRows) : null), [replacingHeaders, headerRows]);
  const patch: DestinationPatchBody = {};
  if (label.trim() !== destination.label) patch.label = label.trim();
  if (url.trim() !== destination.url) patch.url = url.trim();
  if (enabled !== destination.enabled) patch.enabled = enabled;
  if (replacingHeaders) patch.headers = contentfulHeaders(headerRows);
  const dirty = Object.keys(patch).length > 0;
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

  async function save() {
    if (blockOn() || saving || !dirty) return;
    setAttempted(false);
    setSaving(true);
    setServerError(null);
    setUrlRefusal(null);
    setHeadersRefusal(null);
    const res = await updateWebhook(destination.id, patch);
    setSaving(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      setReplacingHeaders(false);
      setHeaderRowsState([]);
      onSaved(res.data);
      toast.success("Destination saved.");
      return;
    }
    // Tier 1: the server's detail renders VERBATIM, at the surface its
    // code names when that surface is knowable.
    if (res.code === WEBHOOK_URL_BLOCKED_CODE) setUrlRefusal(res.message);
    else if (res.code === WEBHOOK_HEADER_RESERVED_CODE) setHeadersRefusal(res.message);
    else setServerError(res.message);
  }

  return {
    label,
    url,
    enabled,
    replacingHeaders,
    headerRows,
    setLabel,
    // A refusal self-clears on the gesture that moves its input.
    setUrl: (value) => {
      setUrlState(value);
      setUrlRefusal(null);
    },
    setEnabled,
    startReplacingHeaders: () => setReplacingHeaders(true),
    // Folding the editor back drops its rows, so nothing from an
    // abandoned replace rides the next save.
    stopReplacingHeaders: () => {
      setReplacingHeaders(false);
      setHeaderRowsState([]);
      setHeadersRefusal(null);
    },
    setHeaderRows: (rows) => {
      setHeaderRowsState(rows);
      setHeadersRefusal(null);
    },
    attempted,
    labelIssue,
    urlIssue,
    headersIssue,
    urlRefusal,
    headersRefusal,
    serverError,
    saving,
    dirty,
    save,
  };
}
