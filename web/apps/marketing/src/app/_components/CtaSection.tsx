import { APP_URL, DOCS_URL } from "../_lib/urls";

export function CtaSection() {
  return (
    <section className="reveal relative px-6 py-16 text-center">
      <div className="mx-auto max-w-5xl overflow-hidden rounded-2xl border border-ink/10 bg-paper-soft px-8 py-14 dark:border-paper/10 dark:bg-paper/5">
        <h2 className="font-display text-3xl tracking-tight text-ink sm:text-4xl dark:text-paper">
          Your next customers look like your last ones.
        </h2>
        <p className="mx-auto mt-4 max-w-md text-lg text-ink/50 dark:text-paper/50">
          Paste a few domains and see the list it builds. First search takes about a minute.
        </p>
        <div className="mt-8 flex flex-col items-center gap-4">
          <a
            href={`${APP_URL}/signup`}
            className="inline-flex items-center rounded-lg bg-signal px-8 py-3.5 text-base font-semibold text-paper transition-colors hover:bg-signal-600"
          >
            Get started
          </a>
          <span className="text-sm text-ink/50 dark:text-paper/50">
            Or{" "}
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="font-semibold text-ink transition-colors hover:text-ink/70 dark:text-paper dark:hover:text-paper/70"
            >
              read the docs
            </a>{" "}
            and self-host it.
          </span>
        </div>
      </div>
    </section>
  );
}
