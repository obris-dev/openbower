"use client";

import { useMemo, useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError } from "@bower/ui";
import {
  postColumnWebhookTest,
  WEBHOOK_DESTINATION_UNKNOWN_CODE,
  WEBHOOK_ROW_UNKNOWN_CODE,
  type ListColumn,
  type WebhookColumnTestResult,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { ColumnPicker } from "./column-picker";
import {
  ADD_AI_COLUMN,
  GAP_DESTINATION,
  GAP_WAIT,
  LEDE,
  NO_AI_COLUMNS_LINE,
  NO_ROWS_LINE,
  WAIT_HINT,
  WAIT_LEGEND,
} from "./copy";
import { DestinationPicker } from "./destination-picker";
import { fromJson, previewEnvelope, previewLines } from "./lib/preview";
import { cellsFor, sampleFrom, type SampleRow } from "./lib/sample";
import { PayloadPreview } from "./payload-preview";

const FORM_ID = "send-webhook-form";
const DESTINATION_ID = "send-webhook-destination-section";
const WAIT_ID = "send-webhook-wait";
const WAIT_ERROR_ID = "send-webhook-wait-error";
const PREVIEW_ID = "send-webhook-preview";

/** The Send webhook drawer: two decisions (destination, the columns to
 * wait for), then the envelope those decisions produce as the
 * centerpiece, where the sample row is chosen and its cells edited in
 * place (values, which columns ride). Its one action is a test send, whose outcome lands on the
 * preview. Mounted fresh per open, so state resets with the gesture
 * and no roster read runs while closed. */
export function SendWebhookDrawer(props: {
  open: boolean;
  onClose: () => void;
  /** The sheet swaps this drawer for its Use AI one: a sheet with no
   * AI column has nothing to wait for, and the next step is adding one. */
  onAddAiColumn: () => void;
  listId: string;
  listLabel: string;
  columns: ListColumn[];
  sampleRows: SampleRow[];
}) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
      onAddAiColumn={props.onAddAiColumn}
      listId={props.listId}
      listLabel={props.listLabel}
      columns={props.columns}
      sampleRows={props.sampleRows}
    />
  );
}

function DrawerContent({
  open,
  afterLeave,
  onClose,
  onAddAiColumn,
  listId,
  listLabel,
  columns,
  sampleRows,
}: {
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
  onAddAiColumn: () => void;
  listId: string;
  listLabel: string;
  columns: ListColumn[];
  sampleRows: SampleRow[];
}) {
  const aiColumns = useMemo(() => columns.filter((column) => column.fill !== null), [columns]);
  const allKeys = useMemo(() => columns.map((column) => column.key), [columns]);
  const labels = useMemo(() => Object.fromEntries(columns.map((column) => [column.key, column.label])), [columns]);
  const [destinationId, setDestinationId] = useState("");
  const [rosterAttempt, setRosterAttempt] = useState(0);
  // Every AI column by default: the common case is "when everything
  // finishes", and the user removes columns to send sooner.
  const [waitKeys, setWaitKeys] = useState<Set<string>>(() => new Set(aiColumns.map((column) => column.key)));
  const [payloadKeys, setPayloadKeys] = useState<Set<string>>(() => new Set(allKeys));
  const [sampleIndex, setSampleIndex] = useState(0);
  const sampleRow = sampleRows[sampleIndex] ?? null;
  // Edits are OVERRIDES over the live row, not a copy of it: a fill
  // landing while the drawer is open refreshes the rows, and the
  // sample follows, with the user's edits still winning.
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const values = { ...sampleFrom(sampleRow, allKeys), ...overrides };
  const [attempted, setAttempted] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<WebhookColumnTestResult | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [destinationRefusal, setDestinationRefusal] = useState<string | null>(null);
  const [sampleRefusal, setSampleRefusal] = useState<string | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const destinationGap = destinationId === "" ? GAP_DESTINATION : null;
  const waitGap = waitKeys.size === 0 ? GAP_WAIT : null;
  const rowsGap = sampleRow === null ? NO_ROWS_LINE : null;
  const ready = !destinationGap && !waitGap && !rowsGap;
  const orderedWait = allKeys.filter((key) => waitKeys.has(key));
  const orderedPayload = allKeys.filter((key) => payloadKeys.has(key));
  const missingColumns = columns.filter((column) => !payloadKeys.has(column.key));

  // The preview: a sent envelope is the truth until any choice changes,
  // then the drawer's own reconstruction with the server's values
  // marked. Derived per render: one row's worth of lines is cheap.
  const lines = sent
    ? previewLines(fromJson(sent.envelope))
    : sampleRow === null
      ? []
      : previewLines(
          previewEnvelope({
            sheet: { id: listId, label: listLabel },
            waitKeys: orderedWait,
            payloadKeys: orderedPayload,
            row: { id: sampleRow.id, position: sampleRow.position },
            values,
            canAddColumns: missingColumns.length > 0,
          }),
        );

  /** Any change makes a sent result stale: the preview goes back to
   * describing what the NEXT send will carry. */
  function touched() {
    setSent(null);
  }

  /** Another row as the sample: its values replace any edits, since
   * edits were about the row they were made on. */
  function stepRow(delta: 1 | -1) {
    const next = sampleIndex + delta;
    const row = sampleRows[next];
    if (!row) return;
    setSampleIndex(next);
    setOverrides({});
    setSampleRefusal(null);
    touched();
  }

  /** Which columns ride: the payload keeps at least one, so the
   * server's empty-payload refusal is unreachable from here. */
  function setPayload(key: string, present: boolean) {
    setPayloadKeys((prev) => {
      const next = new Set(prev);
      if (present) next.add(key);
      else if (next.size > 1) next.delete(key);
      return next;
    });
    touched();
  }

  // The action stays ENABLED and diagnoses here: nothing marks before
  // a first attempt, then the first gap is brought into view.
  function blockOn(): boolean {
    if (ready) return false;
    setAttempted(true);
    const anchor = destinationGap ? DESTINATION_ID : waitGap ? WAIT_ID : PREVIEW_ID;
    document.getElementById(anchor)?.scrollIntoView({ block: "nearest" });
    return true;
  }

  async function sendTest() {
    if (blockOn() || sending || sampleRow === null) return;
    setAttempted(false);
    setSending(true);
    // A new attempt supersedes the last result: a refusal must not land
    // under a row still claiming the previous send got through.
    setSent(null);
    setServerError(null);
    setDestinationRefusal(null);
    setSampleRefusal(null);
    const res = await postColumnWebhookTest(listId, {
      destination_id: destinationId,
      wait_keys: orderedWait,
      payload_keys: orderedPayload,
      row_id: sampleRow.id,
      cells: cellsFor(values, orderedPayload),
    });
    setSending(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      setSent(res.data);
      document.getElementById(PREVIEW_ID)?.scrollIntoView({ block: "nearest" });
      return;
    }
    // Tier 1: the server's detail renders VERBATIM, at the surface its
    // code names when that surface is knowable. A vanished destination
    // re-reads the roster; a vanished row is the sheet's to reload.
    if (res.code === WEBHOOK_DESTINATION_UNKNOWN_CODE) {
      setDestinationRefusal(res.message);
      setDestinationId("");
      setRosterAttempt((n) => n + 1);
    } else if (res.code === WEBHOOK_ROW_UNKNOWN_CODE) setSampleRefusal(res.message);
    else setServerError(res.message);
  }

  const footer = (
    <>
      {serverError && <ErrorMessage message={serverError} />}
      <Button type="submit" form={FORM_ID} fullWidth loading={sending}>
        Send test
      </Button>
    </>
  );

  return (
    <Drawer open={open} onClose={onClose} title="Send webhook" width="xl" afterLeave={afterLeave} footer={footer}>
      <form
        id={FORM_ID}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void sendTest();
        }}
        className="space-y-6"
      >
        <p className="text-sm text-muted">{LEDE}</p>
        <DestinationPicker
          id={DESTINATION_ID}
          value={destinationId}
          onChange={(next) => {
            setDestinationId(next);
            setDestinationRefusal(null);
            touched();
          }}
          attempt={rosterAttempt}
          onRefresh={() => setRosterAttempt((n) => n + 1)}
          warned={attempted && destinationGap !== null}
          gap={attempted ? destinationGap : null}
          refusal={destinationRefusal}
        />
        {aiColumns.length === 0 ? (
          // The empty state is the invitation, with the next step on it;
          // an attempt still answers with its cause rather than silence.
          <div id={WAIT_ID} className="space-y-2">
            <p className="text-sm text-muted">{NO_AI_COLUMNS_LINE}</p>
            <Button type="button" variant="ghost" size="sm" onClick={onAddAiColumn}>
              {ADD_AI_COLUMN}
            </Button>
            {attempted && waitGap && (
              <FieldError id={WAIT_ERROR_ID} tone="warning">
                {waitGap}
              </FieldError>
            )}
          </div>
        ) : (
          <div>
            <ColumnPicker
              id={WAIT_ID}
              legend={WAIT_LEGEND}
              hint={WAIT_HINT}
              columns={aiColumns}
              selected={waitKeys}
              onChange={(next) => {
                setWaitKeys(next);
                touched();
              }}
              warned={attempted && waitGap !== null}
              describedBy={attempted && waitGap ? WAIT_ERROR_ID : undefined}
            />
            {attempted && waitGap && (
              <FieldError id={WAIT_ERROR_ID} tone="warning">
                {waitGap}
              </FieldError>
            )}
          </div>
        )}
        {sampleRow === null ? (
          <p id={PREVIEW_ID} className="text-sm text-muted">
            {NO_ROWS_LINE}
          </p>
        ) : (
          <div className="space-y-2">
            <PayloadPreview
              id={PREVIEW_ID}
              lines={lines}
              result={sent?.delivery ?? null}
              sample={{ position: sampleRow.position, index: sampleIndex, count: sampleRows.length, onStep: stepRow }}
              cells={{
                labels,
                missing: missingColumns,
                removable: payloadKeys.size > 1,
                onEdit: (key, value) => {
                  setOverrides((prev) => ({ ...prev, [key]: value }));
                  setSampleRefusal(null);
                  touched();
                },
                onRemove: (key) => setPayload(key, false),
                onAdd: (key) => setPayload(key, true),
              }}
              onEditAgain={touched}
            />
            {sampleRefusal && <FieldError>{sampleRefusal}</FieldError>}
          </div>
        )}
      </form>
    </Drawer>
  );
}
