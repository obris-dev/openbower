import type { ReactNode } from "react";

// One capability per showcase, the headline carrying the outcome and
// the body one plain sentence of what happens.
type Showcase = { title: string; body: string; mock: ReactNode };

const SHOWCASES: Showcase[] = [
  {
    title: "Put an expert on every detail.",
    body: "Each detail that matters gets its own agent: the decision maker, the last raise, what changed this quarter.",
    mock: <AgentCard />,
  },
  {
    title: "New rows research themselves.",
    body: "Add rows by hand, webhook, or CSV, and your agents add the details the moment they land.",
    mock: <ArrivalCard />,
  },
  {
    title: "Get pinged when the research is ready.",
    body: "No watching the sheet. When a row completes, it meets you where you work: your CRM, your sequencer or an automation. Then you say hello.",
    mock: <DeliveryCard />,
  },
];

const CARD = "rounded-xl border border-ink/10 bg-paper shadow-xl shadow-black/5 dark:border-paper/10 dark:bg-ink dark:shadow-black/20";

// The three mocks are one row (vertex.io) in three states, on the same
// columns: the agent that owns the person column, the row landing with
// that agent's answer in and the web research still running, and the
// finished row leaving. One grid constant keeps the columns aligned
// from state to state.
const SHEET_GRID = "grid grid-cols-[1fr_1.3fr_1.1fr] gap-x-3 px-4";
const HEADERS = ["Company", "Decision maker", "Last fundraise"];
const SETTLED: [string, string, string][] = [
  ["acme.com", "Dana Wells, VP Sales", "$18M Series A"],
  ["globex.com", "Priya Shah, Head of Growth", "$42M Series B"],
];

function SheetHeader() {
  return (
    <div
      className={`${SHEET_GRID} border-b border-ink/10 py-2 text-[11px] font-medium uppercase tracking-wide text-ink/40 dark:border-paper/10 dark:text-paper/40`}
    >
      {HEADERS.map((header) => (
        <span key={header} className="truncate">
          {header}
        </span>
      ))}
    </div>
  );
}

function SettledRows() {
  return SETTLED.map(([domain, contact, fundraise]) => (
    <div key={domain} className={`${SHEET_GRID} border-b border-ink/5 py-2.5 text-sm last:border-0 dark:border-paper/5`}>
      <span className="truncate font-medium text-signal">{domain}</span>
      <span className="truncate text-ink/80 dark:text-paper/80">{contact}</span>
      <span className="truncate tabular-nums text-ink/70 dark:text-paper/70">{fundraise}</span>
    </div>
  ));
}

function AgentCard() {
  return (
    <div className={`space-y-3 p-5 ${CARD}`}>
      <p className="text-sm font-semibold text-ink dark:text-paper">Decision-maker finder</p>
      <p className="rounded-lg bg-ink/5 px-3 py-2 font-mono text-xs text-ink/60 dark:bg-paper/10 dark:text-paper/60">
        {"Find the most senior revenue leader at {{name}} ({{domain}})."}
      </p>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded-full bg-signal/10 px-2.5 py-1 font-medium text-signal">Find contacts</span>
        <span className="rounded-full bg-signal/10 px-2.5 py-1 font-medium text-signal">Web search</span>
        <span className="rounded-full bg-ink/5 px-2.5 py-1 text-ink/50 dark:bg-paper/10 dark:text-paper/50">
          Ollama | gemma
        </span>
      </div>
      <div className="flex items-center gap-2 border-t border-ink/10 pt-3 text-xs dark:border-paper/10">
        <span className="text-ink/50 dark:text-paper/50">Output</span>
        <span className="rounded-md border border-ink/15 px-2 py-0.5 font-medium text-ink/80 dark:border-paper/15 dark:text-paper/80">
          Decision maker
        </span>
      </div>
    </div>
  );
}

/** The row landing: the quick web lookup already back, the contact
 * search (the slower job) still running. Agents run independently, so
 * the order cells resolve in is not the column order. */
function ArrivalCard() {
  return (
    <div className={`overflow-hidden ${CARD}`}>
      <SheetHeader />
      <div className={`${SHEET_GRID} items-center border-b border-ink/5 bg-signal/5 py-2.5 text-sm dark:border-paper/5`}>
        <span className="flex min-w-0 items-center gap-1.5 font-medium text-signal">
          <span className="truncate">vertex.io</span>
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-signal/70">just now</span>
        </span>
        <span aria-hidden className="h-3.5 w-4/5 rounded bg-ink/10 dark:bg-paper/10" />
        <span className="truncate tabular-nums text-ink/70 underline decoration-ink/20 decoration-dotted underline-offset-2 dark:text-paper/70 dark:decoration-paper/20">
          $25M Series B
        </span>
      </div>
      <SettledRows />
    </div>
  );
}

/** The same row finished and gone out: every cell resolved, then the
 * delivery line, a receipt (destination, status, when), not a log. */
function DeliveryCard() {
  return (
    <div className={`overflow-hidden ${CARD}`}>
      <SheetHeader />
      <div className={`${SHEET_GRID} border-b border-ink/5 py-2.5 text-sm dark:border-paper/5`}>
        <span className="truncate font-medium text-signal">vertex.io</span>
        <span className="truncate text-ink/80 dark:text-paper/80">Nadia Okoye, VP Sales</span>
        <span className="truncate tabular-nums text-ink/70 dark:text-paper/70">$25M Series B</span>
      </div>
      <div className="flex items-center gap-2 bg-signal/5 px-4 py-2.5 text-xs">
        <svg
          className="h-3.5 w-3.5 shrink-0 text-signal"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
        <span className="font-medium text-ink dark:text-paper">Sent</span>
        <span className="truncate font-mono text-ink/50 dark:text-paper/50">POST hooks.acme.com/openbower</span>
        <span className="ml-auto shrink-0 text-ink/40 dark:text-paper/40">just now</span>
      </div>
    </div>
  );
}

export function WorkflowSection() {
  return (
    <section id="how-it-works" className="px-6 py-24">
      <div className="mx-auto max-w-5xl">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          More time where it matters.
        </h2>
        <p className="mt-4 max-w-2xl text-lg text-ink/60 dark:text-paper/60">
          OpenBower is a spreadsheet that does its own research. Every column is a key detail, agents dig them
          up, and you focus on the close.
        </p>

        {SHOWCASES.map((showcase, index) => {
          const mockFirst = index % 2 === 1;
          const text = (
            <div className="mx-auto max-w-sm lg:max-w-none">
              <h3 className="text-2xl font-bold text-ink dark:text-paper">{showcase.title}</h3>
              <p className="mt-3 text-base leading-relaxed text-ink/60 dark:text-paper/60">{showcase.body}</p>
            </div>
          );
          const mock = <div className="mx-auto max-w-sm lg:max-w-none">{showcase.mock}</div>;
          return (
            <div key={showcase.title} className="reveal mt-16 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
              <div className={mockFirst ? "order-2 lg:order-1" : ""}>{mockFirst ? mock : text}</div>
              <div className={mockFirst ? "order-1 lg:order-2" : ""}>{mockFirst ? text : mock}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
