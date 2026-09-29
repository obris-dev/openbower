import { DOCS_URL } from "../_lib/urls";
import { GithubLink } from "./GithubLink";
import { WaitlistForm } from "./WaitlistForm";

export function CtaSection() {
  return (
    <section className="reveal relative px-6 py-16 text-center">
      <div className="mx-auto max-w-5xl border-t border-ink/10 px-8 py-20 dark:border-paper/10">
        <h2 className="font-display text-4xl tracking-tight text-ink sm:text-5xl dark:text-paper">
          Automate everything up to hello.
        </h2>
        <p className="mx-auto mt-4 max-w-md text-lg text-ink/60 dark:text-paper/60">
          Clone the repo and start gathering the details that power your outreach.
        </p>
        <div className="mt-8 flex flex-col items-center gap-4">
          <GithubLink
            iconClassName="h-4 w-4"
            className="inline-flex items-center gap-2 rounded-lg bg-signal px-8 py-3.5 text-base font-semibold text-paper transition-colors hover:bg-signal-600"
          >
            Get the repo
          </GithubLink>
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
            first.
          </span>
        </div>

        <div id="waitlist" className="mx-auto mt-14 max-w-md scroll-mt-24 border-t border-ink/10 pt-10 dark:border-paper/10">
          <p className="text-sm font-semibold text-ink dark:text-paper">Don&rsquo;t want to run it yourself?</p>
          <p className="mt-1 text-sm text-ink/60 dark:text-paper/60">Join the waitlist for the hosted version.</p>
          <div className="mt-4">
            <WaitlistForm source="cta" />
          </div>
        </div>
      </div>
    </section>
  );
}
