"use client";

import { useEffect, useState } from "react";
import { Button, Drawer, ErrorMessage, Skeleton } from "@bower/ui";
import { apiRoutes, buildApiUrl, fetchIngestSchema, type IngestSchema } from "@bower/api";

import { PUSH_CREDENTIAL, PushConnect } from "../../../_components/push-connect";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

const TITLE = "Add rows via API";
const LEDE =
  "Your own script adds rows to this sheet with one request. Rows land within seconds and start the sheet's workflow, so AI columns fill as if you had typed them.";
const NO_COLUMNS =
  "This sheet has no columns a request can set yet, so there is nothing to send. Add one from Add column with a type such as Text or URL, and its key appears here with the command.";

/** The endpoint, as a page would document it, read off the sheet
 * itself: one command that runs as pasted once the key is set, with a
 * sample row that passes every column's shape rule, and the keys the
 * row is keyed on. CONTROLLED: the actions menu summons it. Mounted
 * fresh per open, so a failed read never outlives the gesture and the
 * schema is read once per open; the unmount trails the leave motion. */
export function AddRowsDrawer(props: { sheetId: string; open: boolean; onClose: () => void }) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      sheetId={props.sheetId}
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
    />
  );
}

function DrawerContent({
  sheetId,
  open,
  afterLeave,
  onClose,
}: {
  sheetId: string;
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
}) {
  const [schema, setSchema] = useState<IngestSchema | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const url = buildApiUrl(apiRoutes.sheets.ingest(sheetId));

  useEffect(() => {
    let current = true;
    async function load() {
      const res = await fetchIngestSchema(sheetId);
      if (!current) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status === "ok") setSchema(res.data);
      else setFailure(res.message);
    }
    void load();
    return () => {
      current = false;
    };
  }, [sheetId]);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={TITLE}
      width="lg"
      afterLeave={afterLeave}
      footer={
        <Button type="button" fullWidth onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="space-y-6">
        <p className="text-sm text-muted">{LEDE}</p>
        {failure && <ErrorMessage message={failure} />}
        {!schema && !failure && <Skeleton className="h-32" />}
        {schema && schema.columns.length === 0 && <p className="text-sm text-warning">{NO_COLUMNS}</p>}
        {schema && schema.columns.length > 0 && (
          <PushConnect url={url} columns={schema.columns} credential={PUSH_CREDENTIAL.API_KEY} />
        )}
      </div>
    </Drawer>
  );
}
