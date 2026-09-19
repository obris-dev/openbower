"use client";

import { useMemo, useState } from "react";
import { Button, Drawer, ErrorMessage, FieldError, Input, Label, Select, Skeleton, Switch } from "@bower/ui";
import {
  COLUMN_EXISTS_CODE,
  DEFAULT_WEBHOOK_CADENCE_SECONDS,
  DERIVED_KEY_COLLISION_CODE,
  RESERVED_KEY_CODE,
  WEBHOOK_CADENCE_SECONDS,
  WEBHOOK_COLUMN_NOT_AI_CODE,
  WEBHOOK_COLUMN_UNKNOWN_CODE,
  WEBHOOK_DESTINATION_UNKNOWN_CODE,
  WEBHOOK_ROW_UNKNOWN_CODE,
  COLUMN_LABEL_MAX_LENGTH,
  postColumnWebhookTest,
  type ApiResult,
  type ListColumn,
  type WebhookColumnBody,
  type WebhookColumnConfigWire,
  type WebhookColumnPatchBody,
  type WebhookColumnTestBody,
  type WebhookColumnTestResult,
} from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { ColumnPicker } from "./column-picker";
import {
  ADD_AI_COLUMN,
  ADD_COLUMN_ACTION,
  ADD_TITLE,
  CADENCE_HINT,
  CADENCE_LABEL,
  COLUMN_NAME_LABEL,
  COLUMN_NAME_PLACEHOLDER,
  CONFIG_FAILED_LINE,
  EDIT_LEDE,
  EDIT_TITLE,
  ENABLED_HINT,
  ENABLED_LABEL,
  GAP_COLUMN_NAME,
  GAP_DESTINATION,
  GAP_WAIT,
  LEDE,
  NO_AI_COLUMNS_LINE,
  NO_ROWS_LINE,
  PREVIEW_FAILED_LINE,
  PREVIEW_WAITING_LINE,
  RETRY,
  SAVE_ACTION,
  SEND_TEST_ACTION,
  WAIT_HINT,
  WAIT_LEGEND,
} from "./copy";
import { exportableColumns } from "../lib/exportable-columns";
import { DestinationPicker } from "./destination-picker";
import { cadenceLabel, cadenceOptions } from "./lib/cadence";
import { bodyFor, initialDraft, isDirty, withSiblings, type WebhookDraft } from "./lib/config";
import { fromJson, previewLines, withEditableCells } from "./lib/preview";
import { cellsFor, sampleFrom, type SampleRow } from "./lib/sample";
import { PayloadPreview } from "./payload-preview";
import { usePreview } from "./use-preview";
import { useWebhookConfig, type WebhookConfigLoad } from "./use-webhook-config";
import type { ColumnOutcome } from "../use-columns";

const FORM_ID = "send-webhook-form";
const NAME_ID = "send-webhook-name";
const NAME_ERROR_ID = "send-webhook-name-error";
const DESTINATION_ID = "send-webhook-destination-section";
const WAIT_ID = "send-webhook-wait";
const WAIT_ERROR_ID = "send-webhook-wait-error";
const PREVIEW_ID = "send-webhook-preview";
const CADENCE_ID = "send-webhook-cadence";
const ENABLED_ID = "send-webhook-enabled";

type Props = {
  open: boolean;
  onClose: () => void;
  /** The sheet swaps this drawer for its Use AI one: a sheet with no
   * AI column has nothing to wait for, and the next step is adding one. */
  onAddAiColumn: () => void;
  /** The webhook column being edited; null means Add. */
  column: ListColumn | null;
  onAdd: (body: WebhookColumnBody) => Promise<ColumnOutcome>;
  onSave: (key: string, body: WebhookColumnPatchBody) => Promise<ColumnOutcome>;
  listId: string;
  listLabel: string;
  columns: ListColumn[];
  sampleRows: SampleRow[];
};

/** The Send webhook drawer: two decisions (destination, the columns to
 * wait for), then the envelope those decisions produce as the
 * centerpiece, where the sample row is chosen and its cells edited in
 * place. Its primary action adds the column (or saves an existing
 * one); Send test sends one row now. Mounted fresh per open, so state
 * resets with the gesture and no read runs while closed. */
export function SendWebhookDrawer(props: Props) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return <DrawerContent {...props} afterLeave={() => setMounted(props.open)} />;
}

function DrawerContent(props: Props & { afterLeave: () => void }) {
  // A mount-time snapshot: the sheet nulls its drawer state on close
  // while the panel is still sliding out, and a live prop would re-dress
  // the leaving panel as the other mode.
  const [column] = useState(props.column);
  // One editor whatever the load state, so the Drawer element under it
  // never changes type: a swap would remount the panel mid-transition,
  // replay its entrance, and on an Escape during the load leave
  // `afterLeave` unfired and this snapshot alive for the next open.
  const { load, retry } = useWebhookConfig(props.listId, column?.key ?? null);
  return <WebhookEditor {...props} column={column} load={load} retryLoad={retry} />;
}

/** The editor's shape, held while its config is on the way. */
function EditorSkeleton() {
  return (
    <div className="space-y-6" aria-hidden>
      <Skeleton className="h-4 w-3/4 motion-reduce:animate-none" />
      <Skeleton className="h-9 w-full motion-reduce:animate-none" />
      <div className="grid gap-2 sm:grid-cols-2">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-6 w-full motion-reduce:animate-none" />
        ))}
      </div>
      <Skeleton className="h-40 w-full motion-reduce:animate-none" />
    </div>
  );
}

function WebhookEditor({
  open,
  afterLeave,
  onClose,
  onAddAiColumn,
  column,
  load,
  retryLoad,
  onAdd,
  onSave,
  listId,
  columns,
  sampleRows,
}: Props & { afterLeave: () => void; load: WebhookConfigLoad; retryLoad: () => void }) {
  const ready = load.status === "ready";
  const saved = load.status === "ready" ? load.config : null;
  const aiColumns = useMemo(() => columns.filter((c) => c.kind === "ai"), [columns]);
  const allKeys = useMemo(() => columns.map((c) => c.key), [columns]);
  const labels = useMemo(() => Object.fromEntries(columns.map((c) => [c.key, c.label])), [columns]);
  const [draft, setDraft] = useState<WebhookDraft>(() =>
    initialDraft(columns, saved, { intervalSeconds: DEFAULT_WEBHOOK_CADENCE_SECONDS }),
  );
  // The draft seeds from the config the moment it arrives; setting
  // state during render is how this file already latches its mount.
  const [seededFrom, setSeededFrom] = useState<WebhookColumnConfigWire | null | undefined>(undefined);
  if (ready && seededFrom !== saved) {
    setSeededFrom(saved);
    setDraft(initialDraft(columns, saved, { intervalSeconds: DEFAULT_WEBHOOK_CADENCE_SECONDS }));
  }
  const [label, setLabel] = useState("");
  const [rosterAttempt, setRosterAttempt] = useState(0);
  const [sampleIndex, setSampleIndex] = useState(0);
  const sampleRow = sampleRows[sampleIndex] ?? null;
  // Edits are OVERRIDES over the live row, not a copy of it: a fill
  // landing while the drawer is open refreshes the rows, and the
  // sample follows, with the user's edits still winning.
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const values = { ...sampleFrom(sampleRow, allKeys), ...overrides };
  // Which action last diagnosed a gap: each marks only the gaps it blocks on.
  const [attempted, setAttempted] = useState<"submit" | "test" | null>(null);
  const [sending, setSending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState<WebhookColumnTestResult | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [labelRefusal, setLabelRefusal] = useState<string | null>(null);
  const [destinationRefusal, setDestinationRefusal] = useState<string | null>(null);
  const [waitRefusal, setWaitRefusal] = useState<string | null>(null);
  const [sampleRefusal, setSampleRefusal] = useState<string | null>(null);

  // Derived, never duplicated: each gap is a memo over its source.
  const nameGap = column === null && label.trim() === "" ? GAP_COLUMN_NAME : null;
  const destinationGap = draft.destinationId === "" ? GAP_DESTINATION : null;
  const waitGap = draft.waitKeys.size === 0 ? GAP_WAIT : null;
  const rowsGap = sampleRow === null ? NO_ROWS_LINE : null;
  const body = bodyFor(draft, columns);
  const missingColumns = exportableColumns(columns).filter((c) => !draft.payloadKeys.has(c.key));
  const dirty =
    saved === null || isDirty(draft, initialDraft(columns, saved, { intervalSeconds: DEFAULT_WEBHOOK_CADENCE_SECONDS }));

  // The preview asks the server once the body is previewable; the
  // client only marks the cells it controls on what comes back.
  const previewBody: WebhookColumnTestBody | null =
    !destinationGap && !waitGap && sampleRow !== null
      ? {
          destination_id: body.destination_id,
          wait_keys: body.wait_keys,
          payload_keys: body.payload_keys,
          row_id: sampleRow.id,
          // The row's own values, not the edits: the client marks its
          // edited cells over the answer, so a keystroke is not a request.
          cells: cellsFor(sampleFrom(sampleRow, allKeys), body.payload_keys),
          ...(column ? { key: column.key } : {}),
        }
      : null;
  const preview = usePreview(listId, previewBody);
  const previewLinesNow = preview.envelope
    ? previewLines(
        withEditableCells(preview.envelope, {
          keys: body.payload_keys,
          values,
          canAddColumns: missingColumns.length > 0,
        }),
      )
    : null;

  /** Any change makes a sent result stale: the box goes back to what
   * the NEXT send will carry. */
  function touched() {
    setSent(null);
  }

  function update(patch: Partial<WebhookDraft>) {
    setDraft((prev) => ({ ...prev, ...patch }));
    touched();
  }

  /** Another row as the sample: its values replace any edits, since
   * edits were about the row they were made on. */
  function stepRow(delta: 1 | -1) {
    const next = sampleIndex + delta;
    if (!sampleRows[next]) return;
    setSampleIndex(next);
    setOverrides({});
    setSampleRefusal(null);
    touched();
  }

  /** Which columns ride: the payload keeps at least one, so the
   * server's empty-payload refusal is unreachable from here. */
  function setPayload(key: string, present: boolean) {
    const next = new Set(draft.payloadKeys);
    if (present) next.add(key);
    else if (next.size > 1) next.delete(key);
    update({ payloadKeys: next });
  }

  // The action stays ENABLED and diagnoses here: nothing marks before
  // a first attempt, then the first gap is brought into view. The two
  // actions need different things: a send needs a row, an add needs
  // a name.
  function blockOn(action: "submit" | "test", gaps: (string | null)[], anchors: string[]): boolean {
    const first = gaps.findIndex((gap) => gap !== null);
    if (first === -1) return false;
    setAttempted(action);
    document.getElementById(anchors[first]!)?.scrollIntoView({ block: "nearest" });
    return true;
  }

  function clearRefusals() {
    setServerError(null);
    setLabelRefusal(null);
    setDestinationRefusal(null);
    setWaitRefusal(null);
    setSampleRefusal(null);
  }

  // Tier 1: the server's detail renders VERBATIM, at the surface its
  // code names when that surface is knowable.
  function routeRefusal(code: string, message: string) {
    if (code === WEBHOOK_DESTINATION_UNKNOWN_CODE) {
      setDestinationRefusal(message);
      update({ destinationId: "" });
      setRosterAttempt((n) => n + 1);
    } else if (code === WEBHOOK_ROW_UNKNOWN_CODE) setSampleRefusal(message);
    else if (
      (code === COLUMN_EXISTS_CODE || code === RESERVED_KEY_CODE || code === DERIVED_KEY_COLLISION_CODE) &&
      column === null
    ) {
      setLabelRefusal(message);
    } else if ((code === WEBHOOK_COLUMN_UNKNOWN_CODE || code === WEBHOOK_COLUMN_NOT_AI_CODE) && aiColumns.length > 0) {
      setWaitRefusal(message);
    }
    // A surface that is not on screen (the name field in edit mode,
    // the wait picker with no AI columns) cannot carry the words: the
    // banner, always rendered, does.
    else setServerError(message);
  }

  function routeResult(res: ApiResult<unknown>): boolean {
    if (redirectIfUnauthenticated(res)) return false;
    if (res.status === "ok") return true;
    routeRefusal(res.code ?? "", res.message);
    return false;
  }

  async function sendTest() {
    if (blockOn("test", [destinationGap, waitGap, rowsGap], [DESTINATION_ID, WAIT_ID, PREVIEW_ID]) || sending) return;
    if (sampleRow === null) return;
    setAttempted(null);
    setSending(true);
    // A new attempt supersedes the last result: a refusal must not land
    // under a row still claiming the previous send got through.
    setSent(null);
    clearRefusals();
    const res = await postColumnWebhookTest(listId, {
      destination_id: body.destination_id,
      wait_keys: body.wait_keys,
      payload_keys: body.payload_keys,
      row_id: sampleRow.id,
      cells: cellsFor(values, body.payload_keys),
      ...(column ? { key: column.key } : {}),
    });
    setSending(false);
    if (!routeResult(res) || res.status !== "ok") return;
    setSent(res.data);
    document.getElementById(PREVIEW_ID)?.scrollIntoView({ block: "nearest" });
  }

  async function submit() {
    if (blockOn("submit", [nameGap, destinationGap, waitGap], [NAME_ID, DESTINATION_ID, WAIT_ID]) || submitting) return;
    if (column !== null && !dirty) return;
    setAttempted(null);
    setSubmitting(true);
    clearRefusals();
    const outcome =
      column === null
        ? await onAdd({ ...body, label: label.trim() })
        : await onSave(column.key, { ...body, enabled: draft.enabled });
    setSubmitting(false);
    if (outcome.ok || !outcome.detail) return;
    routeRefusal(outcome.error, outcome.detail);
  }

  const footer = (
    <>
      {serverError && <ErrorMessage message={serverError} />}
      {attempted === "test" && rowsGap && <FieldError tone="warning">{rowsGap}</FieldError>}
      <div className="flex gap-2">
        <Button type="button" variant="ghost" loading={sending} onClick={() => void sendTest()}>
          {SEND_TEST_ACTION}
        </Button>
        <Button type="submit" form={FORM_ID} loading={submitting} disabled={column !== null && !dirty} className="flex-1">
          {column ? SAVE_ACTION : ADD_COLUMN_ACTION}
        </Button>
      </div>
    </>
  );

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={column ? EDIT_TITLE : ADD_TITLE}
      afterLeave={afterLeave}
      footer={ready ? footer : undefined}
    >
      {!ready ? (
        load.status === "failed" ? (
          <div className="space-y-3">
            <ErrorMessage message={CONFIG_FAILED_LINE} />
            <Button type="button" variant="secondary" size="sm" onClick={retryLoad}>
              {RETRY}
            </Button>
          </div>
        ) : (
          <EditorSkeleton />
        )
      ) : (
      <form
        id={FORM_ID}
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        className="space-y-6"
      >
        <p className="text-sm text-muted">{column ? EDIT_LEDE : LEDE}</p>
        {column === null && (
          <div>
            <Label htmlFor={NAME_ID}>{COLUMN_NAME_LABEL}</Label>
            <Input
              id={NAME_ID}
              autoFocus
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
                setLabelRefusal(null);
              }}
              maxLength={COLUMN_LABEL_MAX_LENGTH}
              placeholder={COLUMN_NAME_PLACEHOLDER}
              warned={attempted === "submit" && nameGap !== null}
              invalid={labelRefusal !== null}
              aria-describedby={labelRefusal || (attempted === "submit" && nameGap) ? NAME_ERROR_ID : undefined}
              className="mt-1"
            />
            {labelRefusal ? (
              <FieldError id={NAME_ERROR_ID}>{labelRefusal}</FieldError>
            ) : (
              attempted &&
              nameGap && (
                <FieldError id={NAME_ERROR_ID} tone="warning">
                  {nameGap}
                </FieldError>
              )
            )}
          </div>
        )}
        <DestinationPicker
          id={DESTINATION_ID}
          value={draft.destinationId}
          onChange={(next) => {
            update({ destinationId: next });
            setDestinationRefusal(null);
          }}
          attempt={rosterAttempt}
          onRefresh={() => setRosterAttempt((n) => n + 1)}
          warned={attempted !== null && destinationGap !== null}
          gap={attempted !== null ? destinationGap : null}
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
            {attempted !== null && waitGap && (
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
              selected={draft.waitKeys}
              onChange={(next) => {
                update({ waitKeys: withSiblings(columns, draft.waitKeys, next) });
                setWaitRefusal(null);
              }}
              warned={attempted !== null && waitGap !== null}
              describedBy={waitRefusal || (attempted !== null && waitGap) ? WAIT_ERROR_ID : undefined}
            />
            {waitRefusal ? (
              <FieldError id={WAIT_ERROR_ID}>{waitRefusal}</FieldError>
            ) : (
              attempted &&
              waitGap && (
                <FieldError id={WAIT_ERROR_ID} tone="warning">
                  {waitGap}
                </FieldError>
              )
            )}
          </div>
        )}
        <div>
          <Label htmlFor={CADENCE_ID}>{CADENCE_LABEL}</Label>
          <Select
            id={CADENCE_ID}
            value={String(draft.intervalSeconds)}
            onChange={(event) => update({ intervalSeconds: Number(event.target.value) })}
            className="mt-1"
          >
            {cadenceOptions(WEBHOOK_CADENCE_SECONDS, draft.intervalSeconds).map((seconds) => (
              <option key={seconds} value={seconds}>
                {cadenceLabel(seconds)}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-muted">{CADENCE_HINT}</p>
        </div>
        {column !== null && (
          <div className="flex items-start justify-between gap-4">
            <div>
              <Label htmlFor={ENABLED_ID}>{ENABLED_LABEL}</Label>
              <p className="mt-1 text-xs text-muted">{ENABLED_HINT}</p>
            </div>
            <Switch id={ENABLED_ID} checked={draft.enabled} onChange={(next) => update({ enabled: next })} />
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
              preview={{
                lines: previewLinesNow,
                loading: preview.loading,
                failure: preview.failure ? preview.failure.message || PREVIEW_FAILED_LINE : null,
                onRetry: preview.retry,
                waitingLine: previewBody === null ? PREVIEW_WAITING_LINE : null,
              }}
              result={sent ? { delivery: sent.delivery, lines: previewLines(fromJson(sent.envelope)) } : null}
              sample={{ position: sampleRow.position, index: sampleIndex, count: sampleRows.length, onStep: stepRow }}
              cells={{
                labels,
                missing: missingColumns,
                removable: draft.payloadKeys.size > 1,
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
      )}
    </Drawer>
  );
}
