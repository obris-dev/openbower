export function ProblemSection() {
  return (
    <section className="reveal px-6 py-24">
      <div className="mx-auto max-w-5xl">
        <div className="grid gap-10 lg:grid-cols-[2fr_3fr] lg:gap-16">
          <div>
            <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl lg:sticky lg:top-28 dark:text-paper">
              Lead lists are bought, stale, and shaped like everyone else&apos;s.
            </h2>
          </div>

          <div className="space-y-6">
            <p className="text-base leading-relaxed text-ink/60 dark:text-paper/60">
              <strong className="text-ink dark:text-paper">You buy the same database as your competitors.</strong>{" "}
              Firmographic filters (industry code, headcount band, region) describe what a company files,
              not what it does. Everyone downloads the same rows and sends the same emails.
            </p>

            <p className="text-base leading-relaxed text-ink/60 dark:text-paper/60">
              <strong className="text-ink dark:text-paper">Volume gets mistaken for value.</strong> Twenty
              thousand rows feels productive until your team burns weeks qualifying companies that were
              never a fit, and your domain reputation pays for the misses.
            </p>

            <p className="text-base leading-relaxed text-ink/60 dark:text-paper/60">
              <strong className="text-ink dark:text-paper">Meanwhile the answer is already in your CRM.</strong>{" "}
              Your won deals share something no SIC code captures: what they actually say they do. That
              resemblance is searchable.
            </p>

            <p className="border-l-2 border-signal/40 pl-4 text-base leading-relaxed text-ink/60 dark:text-paper/60">
              The best targeting you own is the customers you already won. OpenBower turns them into the
              query.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
