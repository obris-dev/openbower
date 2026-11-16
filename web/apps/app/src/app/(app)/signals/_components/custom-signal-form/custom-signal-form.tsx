"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, useToast } from "@bower/ui";
import { SHEET_LABEL_MAX_LENGTH, createCustomSignal, webRoutes } from "@bower/api";

import { goToSection, type ChecklistItem } from "../../../_components/readiness";
import { CREATE_TITLE, CREATED_TOAST, NAME_CHECK, NAME_GAP, NameField, Section, SignalFormFooter } from "../form-parts";
import { SignalHeader } from "../signal-header";
import { ColumnsEditor } from "./columns-editor";
import { COLUMNS_CHECK, COLUMNS_HINT, COLUMNS_TITLE, CUSTOM_INTRO, CUSTOM_NAME_HINT } from "./copy";
import { EMPTY_COLUMN, columnsProblem, columnsToSend, type ColumnDraft } from "./lib/columns";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";

const NAME_ID = "custom-signal-label";
const COLUMNS_ID = "custom-signal-columns";

/** Create a custom signal: a name and the columns its sender fills. Gaps
 * mark their section after a blocked Create, with the checklist at the
 * action; a server refusal reads as sent. A create lands on the signal's
 * page, where its webhook URL waits to be copied. */
export function CustomSignalForm() {
  const router = useRouter();
  const toast = useToast();
  const [label, setLabel] = useState("");
  const [columns, setColumns] = useState<ColumnDraft[]>([{ ...EMPTY_COLUMN }]);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  const nameMissing = label.trim() === "";
  const columnsIssue = columnsProblem(columns);
  const checklist: ChecklistItem<"label" | "columns">[] = [
    { key: "label", anchor: NAME_ID, label: NAME_CHECK, missing: nameMissing },
    { key: "columns", anchor: COLUMNS_ID, label: COLUMNS_CHECK, missing: columnsIssue !== null },
  ];
  const firstGap = checklist.find((item) => item.missing);

  async function create() {
    if (busy) return;
    setAttempted(true);
    if (firstGap) {
      goToSection(firstGap.anchor);
      return;
    }
    setRefusal(null);
    setBusy(true);
    const name = label.trim();
    const res = await createCustomSignal({ label: name, columns: columnsToSend(columns) });
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      setBusy(false);
      setRefusal(res.message);
      return;
    }
    // Busy until the page changes: the signal exists, and a second click
    // would create another.
    toast.success(CREATED_TOAST, name);
    router.push(webRoutes.signal(res.data.sheet.id));
    router.refresh();
  }

  const nameGap = attempted && nameMissing;
  return (
    <>
      <SignalHeader title={CREATE_TITLE} parents={[{ label: CREATE_TITLE, href: webRoutes.signalNew }]}>
        <p className="mt-1 text-sm text-muted">{CUSTOM_INTRO}</p>
      </SignalHeader>
      <Card className="min-w-0 divide-y divide-hairline p-0 lg:max-w-3xl">
        <NameField
          id={NAME_ID}
          hint={CUSTOM_NAME_HINT}
          gap={nameGap ? NAME_GAP : null}
          value={label}
          maxLength={SHEET_LABEL_MAX_LENGTH}
          onChange={setLabel}
        />
        <Section title={COLUMNS_TITLE}>
          <p className="-mt-2 text-xs text-muted">{COLUMNS_HINT}</p>
          <ColumnsEditor
            id={COLUMNS_ID}
            columns={columns}
            onChange={setColumns}
            problem={columnsIssue}
            shown={attempted}
          />
        </Section>
      </Card>
      <SignalFormFooter
        refusal={refusal}
        checklist={attempted && firstGap ? checklist : null}
        busy={busy}
        actions={{ mode: "create", onCreate: () => void create() }}
      />
    </>
  );
}
