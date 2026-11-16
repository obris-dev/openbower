"use client";

import { useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { Button, CopyButton } from "@bower/ui";
import { webRoutes, type IngestColumn } from "@bower/api";

import { columnTypeLabel } from "../column-type-meta";
import { PUSH_CREDENTIAL, curlFor, samplePush, type PushCredential } from "./lib/push-command";

const COMMAND_TITLE = "Command";
const KEY_LEAD = "Needs an API key in OPENBOWER_API_KEY. ";
const KEY_LINK = "Create one in Settings";
const REFRESH_SAMPLE = "Refresh sample";
const SAMPLE_NOTE = "Running this adds a real row to the sheet.";
// The envelope's optional fields, documented as parameters.
const FIELDS: { name: string; note: string }[] = [
  { name: "external_id", note: "Optional. Your ID for the row. Sending the same ID again won't add a duplicate." },
  { name: "request_id", note: "Optional. Your ID for this request. Retrying with the same ID won't add the rows twice." },
];
const ACCEPTED_NOTE = "Returns 202 once the rows are accepted. They appear in the sheet within seconds.";
const COLUMNS_TITLE = "Fields";
const COLUMNS_NOTE = "Key each row's cells by these field names. Columns the sheet fills itself, like AI columns, can't be sent.";
const MANAGE_LEAD = "Manage your sheet's columns directly in ";
const MANAGE_LINK = "the sheet";

/** How to push rows to one endpoint, as a page would document it: one
 * command that runs as pasted, with a sample row that passes every
 * column's shape rule (rerolled on Refresh sample, a fresh external_id each
 * time, so running it again lands another row), and the keys a row is
 * keyed on. The caller reads the endpoint's columns (at least one) and
 * says how a push proves itself; it mounts this once they arrive. */
export function PushConnect({
  url,
  columns,
  credential,
  sheetHref,
}: {
  url: string;
  columns: IngestColumn[];
  credential: PushCredential;
  /** Where the columns are managed, when it is not the page already open. */
  sheetHref?: string;
}) {
  const [push, setPush] = useState(() => samplePush(columns));
  const command = curlFor(url, push, credential);
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-foreground">{COMMAND_TITLE}</p>
          <div className="flex items-center gap-1">
            <Button type="button" variant="secondary" size="sm" onClick={() => setPush(samplePush(columns))}>
              <RefreshCw aria-hidden className="mr-1.5 h-4 w-4" />
              {REFRESH_SAMPLE}
            </Button>
            <CopyButton value={command} />
          </div>
        </div>
        <pre className="mt-1 overflow-x-auto rounded-md bg-surface p-3 font-mono text-xs text-foreground">{command}</pre>
        {credential === PUSH_CREDENTIAL.API_KEY && (
          <p className="mt-2 text-xs text-muted">
            {KEY_LEAD}
            <Link href={webRoutes.settingsApiKeys} className="underline hover:text-foreground">
              {KEY_LINK}
            </Link>
            .
          </p>
        )}
        <p className="mt-2 text-xs text-muted">{SAMPLE_NOTE}</p>
        <dl className="mt-3 space-y-1.5 text-xs">
          {FIELDS.map((field) => (
            <div key={field.name} className="flex gap-3">
              <dt className="w-24 shrink-0 font-mono text-foreground">{field.name}</dt>
              <dd className="text-muted">{field.note}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-muted">{ACCEPTED_NOTE}</p>
      </div>
      <div>
        <p className="text-sm font-medium text-foreground">{COLUMNS_TITLE}</p>
        <p className="text-xs text-muted">{COLUMNS_NOTE}</p>
        {sheetHref !== undefined && (
          <p className="mt-1 text-xs text-muted">
            {MANAGE_LEAD}
            <Link href={sheetHref} className="underline hover:text-foreground">
              {MANAGE_LINK}
            </Link>
            .
          </p>
        )}
        <table className="mt-2 w-full text-left text-xs">
          <thead className="text-faint">
            <tr>
              <th className="py-1 pr-3 font-medium">Column</th>
              <th className="py-1 pr-3 font-medium">Field</th>
              <th className="py-1 font-medium">Type</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {columns.map((column) => (
              <tr key={column.key}>
                <td className="py-1.5 pr-3 text-foreground">{column.label}</td>
                <td className="py-1.5 pr-3 font-mono text-foreground">{column.key}</td>
                <td className="py-1.5 text-muted">{columnTypeLabel(column.type)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
