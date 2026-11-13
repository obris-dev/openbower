"use client";

import { useEffect, useEffectEvent, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Card, CheckboxField, FieldError, Input, Label, Select, Slider, Textarea, useToast } from "@bower/ui";
import { FUNDING_STAGES, createLog, webRoutes, type FundingStage, type KnownSignal, type SignalsCatalog } from "@bower/api";

import { redirectIfUnauthenticated } from "@/lib/ensure-ok";
import { Breadcrumbs } from "../../../../_components/breadcrumbs";
import { goToSection } from "../../../../_components/readiness";
import { TITLE as SIGNALS_TITLE, signalType } from "../../../_components/copy";
import {
  AMOUNT_HINT,
  AMOUNT_LABEL,
  AMOUNT_TO,
  CREATED_TOAST,
  INTRO,
  MAX_AMOUNT_LABEL,
  MIN_AMOUNT_LABEL,
  NAME_GAP,
  NAME_HINT,
  NAME_TITLE,
  OPTIONAL,
  PROMPT_HINT,
  PROMPT_LABEL,
  PROMPT_PLACEHOLDER,
  STAGES_HINT,
  STAGES_LABEL,
  STAGE_LABEL,
  THRESHOLD_HINT,
  THRESHOLD_LABEL,
  TITLE,
  TYPE_LABEL,
  WHAT_FITS_TITLE,
  WHICH_ROUNDS_TITLE,
  percent,
} from "./copy";
import { SignalFormFooter } from "./footer";
import {
  AMOUNT_ID,
  ATTEMPT,
  attemptVerdict,
  NAME_ID,
  THRESHOLD_MAX,
  THRESHOLD_STEP,
  checklistFor,
  gapsOf,
  keepsEveryRound,
  nameOf,
  setupFrom,
  setupKey,
  staleNote,
  suggestedName,
  thresholdFrom,
  thresholdMin,
  type Attempt,
  type SignalFormState,
} from "./setup";
import { TestResults } from "./test-results";
import { useSignalTest } from "./use-signal-test";

const TYPE_ID = "signal-type";
const PROMPT_ID = "signal-prompt";
const THRESHOLD_ID = "signal-threshold";
const THRESHOLD_HINT_ID = "signal-threshold-hint";
const AMOUNT_GAP_ID = "signal-amount-gap";
const AMOUNT_HINT_ID = "signal-amount-hint";
const STAGES_HINT_ID = "signal-stages-hint";
const PROMPT_HINT_ID = "signal-prompt-hint";
const NAME_HINT_ID = "signal-label-hint";
const NAME_GAP_ID = "signal-label-gap";
// How long a filter change settles before the preview follows it: one
// request per pause in editing, not one per keystroke.
const AUTO_PREVIEW_DELAY_MS = 500;

/** Create a signal: say which rounds it keeps and, optionally, what fits,
 * watching the newest rounds sort as the setup changes; then name it and
 * Create signal, which opens the sheet it fills. */
export function SignalForm({ catalog }: { catalog: SignalsCatalog }) {
  const router = useRouter();
  const toast = useToast();
  const { limits, signals } = catalog;
  const [signal, setSignal] = useState<KnownSignal>(signals[0]!.signal);
  const kind = signals.find((each) => each.signal === signal) ?? signals[0]!;
  const [form, setForm] = useState<SignalFormState>({
    label: "",
    labelEdited: false,
    stages: new Set<FundingStage>(),
    minAmount: "",
    maxAmount: "",
    prompt: "",
    minConfidence: kind.min_confidence_default,
  });
  const [attempted, setAttempted] = useState<Attempt | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const test = useSignalTest();

  function update(patch: Partial<SignalFormState>) {
    setForm((prev) => ({ ...prev, ...patch }));
  }

  function toggleStage(stage: FundingStage, on: boolean) {
    const stages = new Set(form.stages);
    if (on) stages.add(stage);
    else stages.delete(stage);
    update({ stages });
  }

  const gaps = gapsOf(form);
  // Each blocked attempt's checklist sits at its own action: Create's in
  // the footer, Test's beside the Test button.
  const checklist = attempted ? checklistFor(attempted, gaps) : null;
  const nameGap = attempted === ATTEMPT.CREATE && gaps.label;
  const prompted = form.prompt.trim() !== "";
  const setup = setupFrom(form);
  const key = setupKey(setup);
  const stale = test.run !== null && test.testedKey !== key;
  // The results at the slider's threshold, when their run was judged.
  const testThreshold = test.run !== null && test.run.prompt !== "" ? form.minConfidence : null;

  // A setup with no description costs nothing to try (the filter decides
  // every round at once), so the preview follows it on its own; one with a
  // description waits for Test, which reads each round against it.
  const autoKey = !prompted && gaps.amount === null ? key : null;
  // The wait before the auto-preview starts reads as updating, not as
  // results out of date.
  const pending = autoKey !== null && autoKey !== test.testedKey && test.notice === null;
  // A filter-only setup held back by its amount says so at the field too,
  // since the results point there.
  const heldByAmount = !prompted && test.run !== null && gaps.amount !== null;
  const amountGap = attempted || heldByAmount ? gaps.amount : null;
  const amountDescribedBy = amountGap ? `${AMOUNT_HINT_ID} ${AMOUNT_GAP_ID}` : AMOUNT_HINT_ID;
  const note = staleNote({
    stale,
    updating: test.busy || pending,
    failed: test.notice !== null,
    prompted,
    amountInvalid: gaps.amount !== null,
  });
  const preview = useEffectEvent(() => {
    void test.runTest(signal, setup, limits.preview_max_events);
  });
  useEffect(() => {
    if (autoKey === null) return;
    const timer = setTimeout(preview, AUTO_PREVIEW_DELAY_MS);
    return () => clearTimeout(timer);
  }, [autoKey]);

  /** Judge the attempt; when it is blocked, take the person to the first
   * gap and stop. */
  function blockOn(attempt: Attempt): boolean {
    const verdict = attemptVerdict(attempt, gaps);
    setAttempted(verdict.attempted);
    if (verdict.jumpTo === null) return false;
    goToSection(verdict.jumpTo);
    return true;
  }

  function runTest() {
    if (blockOn(ATTEMPT.TEST)) return;
    void test.runTest(signal, setup, limits.preview_max_events);
  }

  async function create() {
    if (busy || blockOn(ATTEMPT.CREATE)) return;
    setRefusal(null);
    setBusy(true);
    const label = nameOf(form);
    const res = await createLog({ label, signal, ...setup });
    setBusy(false);
    if (redirectIfUnauthenticated(res)) return;
    if (res.status === "error") {
      setRefusal(res.message);
      return;
    }
    toast.success(CREATED_TOAST, label);
    router.push(webRoutes.sheet(res.data.sheet.id));
    router.refresh();
  }

  return (
    <>
      <div className="min-w-0">
        <Breadcrumbs trail={[{ label: SIGNALS_TITLE, href: webRoutes.signals }]} />
        <h1 className="mt-1 text-2xl font-bold text-foreground">{TITLE}</h1>
        <p className="mt-1 text-sm text-muted">{INTRO}</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,28rem)] lg:items-start">
        <Card className="min-w-0 divide-y divide-hairline p-0">
          <Section title={NAME_TITLE} titleFor={NAME_ID}>
            <p id={NAME_HINT_ID} className="-mt-2 text-xs text-muted">
              {NAME_HINT}
            </p>
            <div>
              <Input
                id={NAME_ID}
                maxLength={limits.label_max_length}
                warned={nameGap}
                aria-describedby={nameGap ? `${NAME_HINT_ID} ${NAME_GAP_ID}` : NAME_HINT_ID}
                value={form.labelEdited ? form.label : suggestedName(form)}
                onChange={(event) => update({ label: event.target.value, labelEdited: true })}
                placeholder={suggestedName(form)}
              />
              {nameGap && (
                <FieldError id={NAME_GAP_ID} tone="warning">
                  {NAME_GAP}
                </FieldError>
              )}
            </div>
          </Section>

          <Section title={WHICH_ROUNDS_TITLE}>
            {signals.length > 1 && (
              <div>
                <Label htmlFor={TYPE_ID}>{TYPE_LABEL}</Label>
                <Select
                  id={TYPE_ID}
                  value={signal}
                  onChange={(event) => setSignal(event.target.value as KnownSignal)}
                  className="mt-1"
                >
                  {signals.map((each) => (
                    <option key={each.signal} value={each.signal}>
                      {signalType(each.signal)}
                    </option>
                  ))}
                </Select>
              </div>
            )}

            <fieldset aria-describedby={STAGES_HINT_ID}>
              <legend className="text-sm font-medium text-foreground">{STAGES_LABEL}</legend>
              <p id={STAGES_HINT_ID} className="mt-1 text-xs text-muted">
                {STAGES_HINT}
              </p>
              <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] gap-x-6 gap-y-2">
                {FUNDING_STAGES.map((stage) => (
                  <CheckboxField
                    key={stage}
                    label={STAGE_LABEL[stage]}
                    checked={form.stages.has(stage)}
                    onChange={(on) => toggleStage(stage, on)}
                  />
                ))}
              </div>
            </fieldset>

            <fieldset id={AMOUNT_ID}>
              <legend className="text-sm font-medium text-foreground">{AMOUNT_LABEL}</legend>
              <p id={AMOUNT_HINT_ID} className="mt-1 text-xs text-muted">
                {AMOUNT_HINT}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Input
                  aria-label={MIN_AMOUNT_LABEL}
                  aria-describedby={amountDescribedBy}
                  inputMode="decimal"
                  warned={amountGap !== null}
                  value={form.minAmount}
                  onChange={(event) => update({ minAmount: event.target.value })}
                  placeholder="1,000,000"
                  className="min-w-0 flex-1 basis-32"
                />
                <span className="text-sm text-muted">{AMOUNT_TO}</span>
                <Input
                  aria-label={MAX_AMOUNT_LABEL}
                  aria-describedby={amountDescribedBy}
                  inputMode="decimal"
                  warned={amountGap !== null}
                  value={form.maxAmount}
                  onChange={(event) => update({ maxAmount: event.target.value })}
                  placeholder="25,000,000"
                  className="min-w-0 flex-1 basis-32"
                />
              </div>
              {amountGap && (
                <FieldError id={AMOUNT_GAP_ID} tone="warning">
                  {amountGap}
                </FieldError>
              )}
            </fieldset>
          </Section>

          <Section title={WHAT_FITS_TITLE} aside={OPTIONAL}>
            <div>
              <Label htmlFor={PROMPT_ID}>{PROMPT_LABEL}</Label>
              <p id={PROMPT_HINT_ID} className="mt-1 text-xs text-muted">
                {PROMPT_HINT}
              </p>
              <Textarea
                id={PROMPT_ID}
                aria-describedby={PROMPT_HINT_ID}
                rows={3}
                maxLength={limits.prompt_max_length}
                value={form.prompt}
                onChange={(event) => update({ prompt: event.target.value })}
                placeholder={PROMPT_PLACEHOLDER}
                className="mt-2"
              />
            </div>
            {prompted && (
              <div>
                <div className="flex items-baseline justify-between gap-3">
                  <Label htmlFor={THRESHOLD_ID}>{THRESHOLD_LABEL}</Label>
                  <span className="text-sm tabular-nums text-foreground">{percent(form.minConfidence)}</span>
                </div>
                <p id={THRESHOLD_HINT_ID} className="mt-1 text-xs text-muted">
                  {THRESHOLD_HINT}
                </p>
                <Slider
                  id={THRESHOLD_ID}
                  value={form.minConfidence}
                  min={thresholdMin(kind.min_confidence_default)}
                  max={THRESHOLD_MAX}
                  step={THRESHOLD_STEP}
                  onChange={(raw) => update({ minConfidence: thresholdFrom(raw) })}
                  aria-valuetext={percent(form.minConfidence)}
                  aria-describedby={THRESHOLD_HINT_ID}
                  className="mt-2"
                />
              </div>
            )}
          </Section>

        </Card>

        <TestResults
          events={limits.preview_max_events}
          busy={test.busy || pending}
          run={test.run}
          threshold={testThreshold}
          notice={test.notice}
          stale={stale}
          staleNote={note}
          prompted={prompted}
          everyRound={keepsEveryRound(setup)}
          testChecklist={attempted === ATTEMPT.TEST ? checklist : null}
          onTest={runTest}
        />
      </div>

      <SignalFormFooter
        refusal={refusal}
        checklist={attempted === ATTEMPT.CREATE ? checklist : null}
        busy={busy}
        onCreate={() => void create()}
      />
    </>
  );
}

/** One part of the setup, titled as the signal works. A title naming a
 * single field labels it. */
function Section({
  title,
  aside,
  titleFor,
  children,
}: {
  title: string;
  aside?: string;
  titleFor?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4 p-6">
      <h2 className="flex items-baseline gap-2 text-base font-semibold text-foreground">
        {titleFor ? <label htmlFor={titleFor}>{title}</label> : title}
        {aside && <span className="text-xs font-normal text-muted">{aside}</span>}
      </h2>
      {children}
    </section>
  );
}
