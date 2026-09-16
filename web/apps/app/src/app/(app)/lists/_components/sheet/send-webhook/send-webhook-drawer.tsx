"use client";

import { useMemo, useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError } from "@bower/ui";
import {
  postColumnWebhookTest,
  WEBHOOK_DESTINATION_UNKNOWN_CODE,
  WEBHOOK_ROW_UNKNOWN_CODE,
  type ListColumn,
  type RenderableDelivery,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { DeliveryResult } from "../../../../_components/webhook-delivery";
import { ColumnPicker } from "./column-picker";
import {
  GAP_DESTINATION,
  GAP_PAYLOAD,
  GAP_WAIT,
  NEXT_PHASE_LINE,
  NO_AI_COLUMNS_LINE,
  NO_ROWS_LINE,
  PAYLOAD_HINT,
  PAYLOAD_LEGEND,
  WAIT_HINT,
  WAIT_LEGEND,
} from "./copy";
import { DestinationPicker } from "./destination-picker";
import { cellsFor, sampleFrom, type SampleRow } from "./lib/sample";
import { SampleEditor } from "./sample-editor";

const FORM_ID = "send-webhook-form";
const DESTINATION_ID = "send-webhook-destination-section";
const WAIT_ID = "send-webhook-wait";
const PAYLOAD_ID = "send-webhook-payload";
const SAMPLE_ID = "send-webhook-sample";

/** The Send webhook drawer: what the column will be (destination, the
 * columns to wait for, the columns to send) and its one action, a
 * test send of a sample row. Mounted fresh per open, so state resets
 * with the gesture and no roster read runs while closed. */
export function SendWebhookDrawer(props: {
  open: boolean;
  onClose: () => void;
  listId: string;
  columns: ListColumn[];
  sampleRow: SampleRow | null;
}) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
      listId={props.listId}
      columns={props.columns}
      sampleRow={props.sampleRow}
    />
  );
}

function DrawerContent({
  open,
  afterLeave,
  onClose,
  listId,
  columns,
  sampleRow,
}: {
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
  listId: string;
  columns: ListColumn[];
  sampleRow: SampleRow | null;
}) {
  const aiColumns = useMemo(() => columns.filter((column) => column.fill !== null), [columns]);
  const [destinationId, setDestinationId] = useState("");
  const [rosterAttempt, setRosterAttempt] = useState(0);
  const [waitKeys, setWaitKeys] = useState<Set<string>>(() => new Set());
  const [payloadKeys, setPayloadKeys] = useState<Set<string>>(() => new Set(columns.map((column) => column.key)));
  const [values, setValues] = useState<Record<string, string>>(() =>
    sampleFrom(
      sampleRow,
      columns.map((column) => column.key),
    ),
  );
  const [attempted, setAttempted] = useState(false);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<RenderableDelivery | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [destinationRefusal, setDestinationRefusal] = useState<string | null>(null);
  const [sampleRefusal, setSampleRefusal] = useState<string | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const destinationGap = destinationId === "" ? GAP_DESTINATION : null;
  const waitGap = waitKeys.size === 0 ? GAP_WAIT : null;
  const payloadGap = payloadKeys.size === 0 ? GAP_PAYLOAD : null;
  const rowsGap = sampleRow === null ? NO_ROWS_LINE : null;
  const ready = !destinationGap && !waitGap && !payloadGap && !rowsGap;
  const payloadColumns = columns.filter((column) => payloadKeys.has(column.key));
  const orderedKeys = (selected: ReadonlySet<string>) => columns.map((c) => c.key).filter((key) => selected.has(key));

  // The action stays ENABLED and diagnoses here: nothing marks before
  // a first attempt, then the first gap is brought into view.
  function blockOn(): boolean {
    if (ready) return false;
    setAttempted(true);
    const anchor = destinationGap ? DESTINATION_ID : waitGap ? WAIT_ID : payloadGap ? PAYLOAD_ID : SAMPLE_ID;
    document.getElementById(anchor)?.scrollIntoView({ block: "nearest" });
    return true;
  }

  async function sendTest() {
    if (blockOn() || sending || sampleRow === null) return;
    setAttempted(false);
    setSending(true);
    setServerError(null);
    setDestinationRefusal(null);
    setSampleRefusal(null);
    const payload = orderedKeys(payloadKeys);
    const res = await postColumnWebhookTest(listId, {
      destination_id: destinationId,
      wait_keys: orderedKeys(waitKeys),
      payload_keys: payload,
      row_id: sampleRow.id,
      cells: cellsFor(values, payload),
    });
    setSending(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "ok") {
      setResult(res.data);
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
      <p className="text-xs text-muted">{NEXT_PHASE_LINE}</p>
      {attempted && rowsGap && <FieldError tone="warning">{rowsGap}</FieldError>}
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
        <DestinationPicker
          id={DESTINATION_ID}
          value={destinationId}
          onChange={(next) => {
            setDestinationId(next);
            setDestinationRefusal(null);
          }}
          attempt={rosterAttempt}
          onRefresh={() => setRosterAttempt((n) => n + 1)}
          warned={attempted && destinationGap !== null}
          gap={attempted ? destinationGap : null}
          refusal={destinationRefusal}
        />
        {aiColumns.length === 0 ? (
          <p id={WAIT_ID} className="text-sm text-muted">
            {NO_AI_COLUMNS_LINE}
          </p>
        ) : (
          <div>
            <ColumnPicker
              id={WAIT_ID}
              legend={WAIT_LEGEND}
              hint={WAIT_HINT}
              columns={aiColumns}
              selected={waitKeys}
              onChange={setWaitKeys}
              warned={attempted && waitGap !== null}
            />
            {attempted && waitGap && <FieldError tone="warning">{waitGap}</FieldError>}
          </div>
        )}
        <div>
          <ColumnPicker
            id={PAYLOAD_ID}
            legend={PAYLOAD_LEGEND}
            hint={PAYLOAD_HINT}
            columns={columns}
            selected={payloadKeys}
            onChange={setPayloadKeys}
            warned={attempted && payloadGap !== null}
          />
          {attempted && payloadGap && <FieldError tone="warning">{payloadGap}</FieldError>}
        </div>
        {sampleRow === null ? (
          <p id={SAMPLE_ID} className="text-sm text-muted">
            {NO_ROWS_LINE}
          </p>
        ) : (
          <SampleEditor
            id={SAMPLE_ID}
            columns={payloadColumns}
            values={values}
            onChange={(key, value) => {
              setValues((prev) => ({ ...prev, [key]: value }));
              setSampleRefusal(null);
            }}
            refusal={sampleRefusal}
          />
        )}
        {result && <DeliveryResult delivery={result} />}
      </form>
    </Drawer>
  );
}
