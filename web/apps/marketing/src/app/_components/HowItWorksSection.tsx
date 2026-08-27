import type { ReactNode } from "react";

type Step = { number: string; title: string; body: string; mock: ReactNode };

const STEPS: Step[] = [
  {
    number: "1",
    title: "Start from examples",
    body: "Paste a handful of domains, your best customers, a competitor's, or seed from a saved list. Two is enough; the engine needs corroboration, not volume.",
    mock: (
      <div className="flex flex-wrap gap-2 rounded-xl border border-ink/10 bg-paper p-5 shadow-xl shadow-black/5 dark:border-paper/10 dark:bg-ink dark:shadow-black/20">
        {["acme.com", "globex.com", "initech.io"].map((d) => (
          <span key={d} className="rounded-full bg-signal/10 px-3 py-1.5 text-sm font-medium text-signal">
            {d}
          </span>
        ))}
        <span className="rounded-full border border-dashed border-ink/20 px-3 py-1.5 text-sm text-ink/40 dark:border-paper/20 dark:text-paper/40">
          add another…
        </span>
      </div>
    ),
  },
  {
    number: "2",
    title: "Discover ranks the universe",
    body: "An exact similarity search over millions of company websites, no sampling, no approximation. Mixed examples split into labeled groups, and the list cuts where confidence statistically breaks instead of padding itself.",
    mock: (
      <div className="rounded-xl border border-ink/10 bg-paper p-5 shadow-xl shadow-black/5 dark:border-paper/10 dark:bg-ink dark:shadow-black/20">
        {[96, 88, 79, 71].map((width, index) => (
          <div key={width} className="mb-3 flex items-center gap-3 last:mb-0">
            <span className="w-5 text-right text-xs tabular-nums text-ink/40 dark:text-paper/40">{index + 1}</span>
            <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-ink/5 dark:bg-paper/10">
              <div className="h-full rounded-full bg-signal/70" style={{ width: `${width}%` }} />
            </div>
          </div>
        ))}
        <div className="mt-3 border-t border-dashed border-signal/40 pt-2 text-center text-xs text-ink/40 dark:text-paper/40">
          confidence breaks here, the list stops
        </div>
      </div>
    ),
  },
  {
    number: "3",
    title: "Save it as a working list",
    body: "Results become a real spreadsheet: sheet-typed columns, folders, CSV export. Paste rows in, reorder columns, keep working it, and any list can seed the next search.",
    mock: (
      <div className="overflow-hidden rounded-xl border border-ink/10 bg-paper shadow-xl shadow-black/5 dark:border-paper/10 dark:bg-ink dark:shadow-black/20">
        <div className="grid grid-cols-3 gap-x-3 border-b border-ink/10 px-4 py-2 text-[11px] font-medium uppercase tracking-wide text-ink/40 dark:border-paper/10 dark:text-paper/40">
          <span>Domain</span>
          <span>Industry</span>
          <span>Score</span>
        </div>
        {[
          ["acme.com", "Fleet software", "0.94"],
          ["globex.com", "Freight ops", "0.93"],
          ["initech.io", "Fleet software", "0.91"],
        ].map((row) => (
          <div
            key={row[0]}
            className="grid grid-cols-3 gap-x-3 border-b border-ink/5 px-4 py-2 text-sm last:border-0 dark:border-paper/5"
          >
            <span className="truncate font-medium text-signal">{row[0]}</span>
            <span className="truncate text-ink/60 dark:text-paper/60">{row[1]}</span>
            <span className="tabular-nums text-ink/70 dark:text-paper/70">{row[2]}</span>
          </div>
        ))}
      </div>
    ),
  },
  {
    number: "4",
    title: "Put agents on the rows",
    body: "Reusable research you configure once: a prompt, a model, tools. Find contacts surfaces who to reach at each company; web search grounds answers in evidence. Cells fill row by row, and blanks stay blank rather than invented.",
    mock: (
      <div className="space-y-3 rounded-xl border border-ink/10 bg-paper p-5 shadow-xl shadow-black/5 dark:border-paper/10 dark:bg-ink dark:shadow-black/20">
        <p className="text-sm font-semibold text-ink dark:text-paper">Decision-maker finder</p>
        <p className="rounded-lg bg-ink/5 px-3 py-2 font-mono text-xs text-ink/60 dark:bg-paper/10 dark:text-paper/60">
          {"Find the most senior revenue leader at {{name}} ({{domain}})."}
        </p>
        <div className="flex gap-2 text-xs">
          <span className="rounded-full bg-signal/10 px-2.5 py-1 font-medium text-signal">Find contacts</span>
          <span className="rounded-full bg-signal/10 px-2.5 py-1 font-medium text-signal">Web search</span>
          <span className="rounded-full bg-ink/5 px-2.5 py-1 text-ink/50 dark:bg-paper/10 dark:text-paper/50">
            Ollama | gemma
          </span>
        </div>
      </div>
    ),
  },
];

export function HowItWorksSection() {
  return (
    <section id="how-it-works" className="px-6 py-24">
      <div className="mx-auto max-w-5xl">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          How it works
        </h2>

        {STEPS.map((step, index) => {
          const mockFirst = index % 2 === 1;
          const text = (
            <div className="mx-auto max-w-sm lg:max-w-none">
              <h3 className="flex items-baseline gap-3 text-2xl font-bold text-ink dark:text-paper">
                <span className="font-display text-5xl text-signal/60">{step.number}</span>
                {step.title}
              </h3>
              <p className="mt-3 text-base leading-relaxed text-ink/60 dark:text-paper/60">{step.body}</p>
            </div>
          );
          const mock = <div className="mx-auto max-w-sm lg:max-w-none">{step.mock}</div>;
          return (
            <div key={step.number} className="reveal mt-16 grid items-center gap-10 lg:grid-cols-2 lg:gap-16">
              <div className={mockFirst ? "order-2 lg:order-1" : ""}>{mockFirst ? mock : text}</div>
              <div className={mockFirst ? "order-1 lg:order-2" : ""}>{mockFirst ? text : mock}</div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
