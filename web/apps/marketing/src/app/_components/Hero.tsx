import { GithubLink } from "./GithubLink";

export function Hero() {
  return (
    <section className="relative flex items-center justify-center px-6 pb-20 pt-28 text-center sm:pt-36">
      <div className="pointer-events-none absolute left-1/2 top-16 h-[400px] w-[600px] -translate-x-1/2 bg-[radial-gradient(ellipse,rgba(0,183,195,0.14)_0%,transparent_70%)]" />

      <div className="relative mx-auto max-w-3xl">
        <p className="text-sm font-semibold uppercase tracking-widest text-signal">
          Open-source sales intelligence
        </p>

        <h1 className="mt-4 font-display text-5xl leading-tight tracking-tight text-ink sm:text-7xl sm:leading-[1.05] dark:text-paper">
          Build revenue engines with <span className="text-signal">your AI</span>.
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-lg text-ink/60 dark:text-paper/60">
          Gather any data, automate GTM workflows, and win more customers.
        </p>

        <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
          <a
            href="#waitlist"
            className="inline-flex items-center gap-2 rounded-lg bg-signal px-6 py-3 text-sm font-semibold text-paper transition-colors hover:bg-signal-600"
          >
            Join the waitlist
          </a>
          <GithubLink
            iconClassName="h-4 w-4"
            className="inline-flex items-center gap-2 text-sm font-semibold text-ink/80 transition-colors hover:text-ink dark:text-paper/80 dark:hover:text-paper"
          >
            Self-host it free
          </GithubLink>
        </div>

        <p className="mt-6 text-sm text-ink/50 dark:text-paper/50">Run your workflows with any AI vendor, or model on your laptop.</p>
      </div>
    </section>
  );
}
