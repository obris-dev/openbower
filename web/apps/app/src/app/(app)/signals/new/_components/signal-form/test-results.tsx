import type { ReactNode } from "react";
import { ChevronRight, FlaskConical } from "lucide-react";
import { Button, Card, LocalTime, Spinner, TIME_FORMAT, cn } from "@bower/ui";
import type { ColumnType, PreviewRow, PreviewRun } from "@bower/api";

import {
  EVERY_ROUND,
  OUTCOME_PILL,
  RESULTS_TITLE,
  SLOW_DOWN_RETRY,
  STAGE_LABEL,
  TEST_ACTION,
  TEST_JUDGING,
  TEST_READING,
  UNSURE_HINT,
  checkingTitle,
  matchesTitle,
  notKeptTitle,
  percent,
  promptedIntro,
  testSummary,
  unsureTitle,
} from "./copy";
import { cellHref } from "../../../../_components/cell-link";
import { ReadinessChecklist } from "../../../../_components/readiness";
import { compactAmount } from "./format";
import { groupRows, rowAt, type SignalChecklistItem } from "./setup";
import type { TestNotice } from "./use-signal-test";

// A round's source is a URL scraped from a feed: it links only when its
// shape is one, through the one home for typed links.
const SOURCE_TYPE: ColumnType = "url";

const TONE = {
  success: "text-success",
  warning: "text-warning",
  quiet: "text-muted",
  faint: "text-faint",
  danger: "text-danger",
} as const;

/** The newest rounds tried against the setup, grouped by what the signal
 * would do with them: a filter-only setup fills this on its own as it
 * changes; one with a description waits for Test, which reads each round
 * against it. */
export function TestResults({
  events,
  busy,
  run,
  threshold,
  notice,
  stale,
  staleNote,
  prompted,
  everyRound,
  testChecklist,
  onTest,
}: {
  events: number;
  busy: boolean;
  run: PreviewRun | null;
  /** Where the slider stands, when the run was judged: its rows re-sort
   * there without testing again. */
  threshold: number | null;
  notice: TestNotice | null;
  stale: boolean;
  /** Why the rows are not the setup's, when nothing will update them. */
  staleNote: string | null;
  prompted: boolean;
  everyRound: boolean;
  /** What a blocked Test still needs, shown at the Test button. */
  testChecklist: SignalChecklistItem[] | null;
  onTest: () => void;
}) {
  const rows = run && (threshold === null ? run.rows : run.rows.map((row) => rowAt(row, threshold)));
  const judging = run !== null && run.prompt !== "";
  return (
    <Card className="min-w-0 space-y-3 p-4 lg:sticky lg:top-20">
      <div className="flex items-center justify-between gap-3">
        <h2 id="signal-results-title" className="text-sm font-medium text-foreground">
          {RESULTS_TITLE}
        </h2>
        {prompted && (
          <Button size="sm" variant="secondary" onClick={onTest} loading={busy}>
            <FlaskConical aria-hidden className="mr-1.5 h-4 w-4" />
            {TEST_ACTION}
          </Button>
        )}
      </div>
      {testChecklist && <ReadinessChecklist items={testChecklist} />}
      {prompted && run === null && !busy && <p className="text-sm text-muted">{promptedIntro(events)}</p>}
      {run === null && busy && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Spinner className="h-4 w-4" />
          {TEST_READING}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm text-warning">
          {notice.text}
          {notice.kind === "slow_down" && notice.retryAt && (
            <>
              {" "}
              {SLOW_DOWN_RETRY} <LocalTime iso={notice.retryAt} format={TIME_FORMAT.MINUTE} />.
            </>
          )}
        </p>
      )}
      {rows && (
        <div aria-labelledby="signal-results-title" aria-busy={busy} className="space-y-3">
          <div className="space-y-1">
            <p className="flex items-center gap-2 text-sm text-foreground">
              {testSummary(rows, threshold)}
              {busy && judging && <span className="text-muted">| {TEST_JUDGING}</span>}
              {busy && !judging && <Spinner className="h-3.5 w-3.5 text-muted" />}
            </p>
            {staleNote && <p className="text-xs text-warning">{staleNote}</p>}
            {everyRound && !stale && <p className="text-xs text-muted">{EVERY_ROUND}</p>}
          </div>
          <div className="space-y-3 overflow-y-auto lg:max-h-[calc(100vh-22rem)]">
            <Groups rows={rows} />
          </div>
        </div>
      )}
    </Card>
  );
}

function Groups({ rows }: { rows: PreviewRow[] }) {
  const { matches, unsure, checking, notKept } = groupRows(rows);
  const noneMatched = matches.length === 0 && checking.length === 0;
  return (
    <>
      <Group title={matchesTitle(matches.length, rows.length, checking.length)} rows={matches.length}>
        {matches.map((row) => (
          <Row key={row.event.id} row={row} />
        ))}
      </Group>
      {unsure.length > 0 && (
        <Group title={unsureTitle(unsure.length)} hint={UNSURE_HINT} rows={unsure.length}>
          {unsure.map((row) => (
            <Row key={row.event.id} row={row} tag={<ConfidenceTag row={row} />} />
          ))}
        </Group>
      )}
      {checking.length > 0 && (
        <Group title={checkingTitle(checking.length)} rows={checking.length}>
          {checking.map((row) => (
            <Row key={row.event.id} row={row} tag={<Spinner className="h-4 w-4 text-muted" />} />
          ))}
        </Group>
      )}
      {notKept.length > 0 && (
        // Open on its own only when nothing matched: then why is the
        // question, and the reasons are the answer.
        <details open={noneMatched} className="group">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-muted hover:text-foreground [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden className="h-3.5 w-3.5 transition group-open:rotate-90 motion-reduce:transition-none" />
            {notKeptTitle(notKept.length)}
          </summary>
          <ul className="mt-2 divide-y divide-hairline rounded-lg bg-wash">
            {notKept.map((row) => (
              <Row key={row.event.id} row={row} muted tag={<OutcomeTag row={row} />} />
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Group({ title, hint, rows, children }: { title: string; hint?: string; rows: number; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <div>
        <h3 className="text-xs font-medium text-foreground">{title}</h3>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      {rows > 0 && <ul className="divide-y divide-hairline rounded-lg bg-wash">{children}</ul>}
    </section>
  );
}

function Tag({ className, children }: { className?: string; children: ReactNode }) {
  return <span className={cn("rounded-full bg-surface px-2 py-0.5 text-xs", className)}>{children}</span>;
}

/** Why a round was not kept, in the words of its outcome; none for an
 * outcome this bundle cannot name. */
function OutcomeTag({ row }: { row: PreviewRow }) {
  const pill = OUTCOME_PILL[row.outcome];
  if (!pill) return null;
  return <Tag className={TONE[pill.tone]}>{pill.label}</Tag>;
}

/** How sure the judge was of an unsure round's yes: where the slider
 * would have to sit to keep it. */
function ConfidenceTag({ row }: { row: PreviewRow }) {
  if (row.confidence === null) return null;
  return <Tag className={TONE.warning}>{percent(row.confidence)}</Tag>;
}

function Row({ row, tag, muted = false }: { row: PreviewRow; tag?: ReactNode; muted?: boolean }) {
  const { company, stage, amount, currency } = row.event.data;
  const stageLabel = stage ? STAGE_LABEL[stage] : null;
  const amountLabel = amount === null ? null : compactAmount(amount, currency);
  const facts = [stageLabel, amountLabel].filter(Boolean).join(" | ");
  const { title, url } = row.event.source;
  const href = cellHref(SOURCE_TYPE, url);
  return (
    <li className="flex items-start gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className={cn("text-sm [overflow-wrap:anywhere]", muted ? "text-muted" : "text-foreground")}>
          <span className={muted ? undefined : "font-medium"}>{company.name}</span>
          {facts && <span className="text-muted"> | {facts}</span>}
        </p>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="block truncate text-xs text-muted hover:text-signal"
            title={title}
          >
            {title}
          </a>
        ) : (
          <p className="truncate text-xs text-muted" title={title}>
            {title}
          </p>
        )}
        {row.reason && <p className="line-clamp-3 text-xs text-muted [overflow-wrap:anywhere]">{row.reason}</p>}
      </div>
      {tag && <span className="shrink-0">{tag}</span>}
    </li>
  );
}
