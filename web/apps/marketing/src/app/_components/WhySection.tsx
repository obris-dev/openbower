// The open-source block every self-hostable tool carries, under the
// header the category uses: the code, the model, the environment, the
// bill. The belief behind them opens pricing.
const REASONS = [
  {
    title: "Read the source.",
    body: "Everything that runs your sheet is on GitHub. Read it, run it or fork it for your needs.",
  },
  {
    title: "Bring your own model.",
    body: "Any OpenAI or Anthropic compatible endpoint: a model on your laptop, your own deployment, or the vendor itself.",
  },
  {
    title: "Your data stays yours.",
    body: "Self-host it and your agents run in your environment. The only traffic out is the vendor calls you configured.",
  },
  {
    title: "No credits.",
    body: "You get as much research as your compute allows, at the vendor's price.",
  },
];

export function WhySection() {
  return (
    <section className="reveal px-6 py-16">
      <div className="mx-auto max-w-5xl">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          Open source and self-hostable.
        </h2>
        <dl className="mt-12">
          {REASONS.map((reason) => (
            <div
              key={reason.title}
              className="grid gap-2 border-t border-ink/10 py-6 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] sm:gap-10 dark:border-paper/10"
            >
              <dt className="text-lg font-bold text-ink dark:text-paper">{reason.title}</dt>
              <dd className="text-base leading-relaxed text-ink/60 dark:text-paper/60">{reason.body}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
