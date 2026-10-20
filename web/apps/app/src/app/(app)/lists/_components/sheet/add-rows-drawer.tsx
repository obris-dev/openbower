"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Button, CopyButton, Drawer, ErrorMessage, Skeleton } from "@bower/ui";
import { apiRoutes, buildApiUrl, fetchIngestSchema, webRoutes, type IngestSchema } from "@bower/api";

import { columnTypeLabel } from "../../../_components/column-type-meta";
import { curlFor } from "./add-rows-command";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

const TITLE = "Add rows via API";
const LEDE =
  "Your own script adds rows to this sheet with one request. Rows land within seconds and start the sheet's workflow, so AI columns fill as if you had typed them.";
const COMMAND_TITLE = "Command";
const KEY_LEAD = "Set OPENBOWER_API_KEY to a key first. ";
const KEY_LINK = "Create one in Settings";
const SAMPLE_NOTE = "The sample row is real: it lands in the sheet and starts its AI columns, so put your own values in first.";
const EVENT_ID_NOTE =
  "Add an event_id to a request to make a retry safe: the same id lands once. Answers 202 with the id once the rows are accepted.";
const COLUMNS_TITLE = "Row keys";
const COLUMNS_NOTE = "Each row is an object keyed on these. Columns the sheet fills itself are not here, and a request naming one is refused.";
const NO_COLUMNS =
  "This sheet has no columns a request can set yet, so there is nothing to send. Add one from Add column with a type such as Text or URL, and its key appears here with the command.";

/** The endpoint, as a page would document it, read off the sheet
 * itself: one command that runs as pasted once the key is set, with a
 * sample row that passes every column's shape rule, and the keys the
 * row is keyed on. CONTROLLED: the actions menu summons it. Mounted
 * fresh per open, so a failed read never outlives the gesture and the
 * schema is read once per open; the unmount trails the leave motion. */
export function AddRowsDrawer(props: { listId: string; open: boolean; onClose: () => void }) {
  const [mounted, setMounted] = useState(props.open);
  if (props.open && !mounted) setMounted(true);
  if (!mounted) return null;
  return (
    <DrawerContent
      listId={props.listId}
      open={props.open}
      afterLeave={() => setMounted(props.open)}
      onClose={props.onClose}
    />
  );
}

function DrawerContent({
  listId,
  open,
  afterLeave,
  onClose,
}: {
  listId: string;
  open: boolean;
  afterLeave: () => void;
  onClose: () => void;
}) {
  const [schema, setSchema] = useState<IngestSchema | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const url = buildApiUrl(apiRoutes.lists.ingest(listId));

  useEffect(() => {
    let current = true;
    async function load() {
      const res = await fetchIngestSchema(listId);
      if (!current) return;
      if (redirectIfUnauthenticated(res)) return;
      if (res.status === "ok") setSchema(res.data);
      else setFailure(res.message);
    }
    void load();
    return () => {
      current = false;
    };
  }, [listId]);

  const command = schema && schema.columns.length > 0 ? curlFor(url, schema.columns) : null;

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
        {command && (
          <div>
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-foreground">{COMMAND_TITLE}</p>
              <CopyButton value={command} />
            </div>
            <pre className="mt-1 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs text-foreground">{command}</pre>
            <p className="mt-2 text-xs text-muted">
              {KEY_LEAD}
              <Link href={webRoutes.settingsApiKeys} className="underline hover:text-foreground">
                {KEY_LINK}
              </Link>
              .
            </p>
            <p className="mt-1 text-xs text-muted">{SAMPLE_NOTE}</p>
            <p className="mt-1 text-xs text-muted">{EVENT_ID_NOTE}</p>
          </div>
        )}
        {schema && schema.columns.length > 0 && (
          <div>
            <p className="text-sm font-medium text-foreground">{COLUMNS_TITLE}</p>
            <p className="text-xs text-muted">{COLUMNS_NOTE}</p>
            <table className="mt-2 w-full text-left text-xs">
              <thead className="text-faint">
                <tr>
                  <th className="py-1 pr-3 font-medium">Column</th>
                  <th className="py-1 pr-3 font-medium">Key</th>
                  <th className="py-1 font-medium">Type</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                {schema.columns.map((column) => (
                  <tr key={column.key}>
                    <td className="py-1.5 pr-3 text-foreground">{column.label}</td>
                    <td className="py-1.5 pr-3 font-mono text-foreground">{column.key}</td>
                    <td className="py-1.5 text-muted">{columnTypeLabel(column.type)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Drawer>
  );
}
