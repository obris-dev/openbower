const REASONS = [
  {
    title: "Honest cutoffs.",
    description:
      "The list ends where the statistics say the resemblance does, per group, not at a round number chosen to look generous.",
  },
  {
    title: "Your examples are the spec.",
    description:
      "Similarity to companies you actually won, computed from what their websites say, not the industry-code proxies everyone else filters on.",
  },
  {
    title: "Open source, your data.",
    description:
      "Self-host it, read it, extend it. Bring your own AI (local Ollama or your provider keys) and pay no per-row toll to work your own lists.",
  },
  {
    title: "Agents that show their work.",
    description:
      "Research cells carry where the answer came from, and an agent that finds nothing leaves the cell empty instead of inventing one.",
  },
];

export function WhySection() {
  return (
    <section className="reveal px-6 py-16">
      <div className="mx-auto max-w-5xl">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          Why teams pick OpenBower
        </h2>
        <div className="mt-12 grid gap-x-16 gap-y-10 sm:grid-cols-2">
          {REASONS.map((reason) => (
            <div key={reason.title}>
              <h3 className="text-lg font-bold text-ink dark:text-paper">{reason.title}</h3>
              <p className="mt-2 text-base leading-relaxed text-ink/60 dark:text-paper/60">
                {reason.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
