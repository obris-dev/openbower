import { APP_URL, DOCS_URL } from "../_lib/urls";
import { GithubLink } from "./GithubLink";

export function Hero() {
  return (
    <section className="relative flex items-center justify-center px-6 pb-20 pt-28 text-center sm:pt-36">
      <div className="pointer-events-none absolute left-1/2 top-16 h-[400px] w-[600px] -translate-x-1/2 bg-[radial-gradient(ellipse,rgba(0,183,195,0.14)_0%,transparent_70%)]" />

      <div className="relative mx-auto max-w-3xl">
        <h1 className="font-display text-5xl leading-tight tracking-tight text-ink sm:text-7xl sm:leading-[1.05] dark:text-paper">
          Prospecting that starts from <span className="text-signal">your best customers</span>.
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-lg text-ink/60 dark:text-paper/60">
          Hand OpenBower a few companies you wish you had more of. It searches millions of company
          websites for the ones most like them, and stops where the resemblance does.
        </p>

        <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
          <a
            href={`${APP_URL}/signup`}
            className="inline-flex items-center gap-2 rounded-lg bg-signal px-6 py-3 text-sm font-semibold text-paper transition-colors hover:bg-signal-600"
          >
            Get started
          </a>
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-sm font-semibold text-signal transition-colors hover:text-signal-600"
          >
            Read the docs
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          </a>
        </div>

        <p className="mt-6 text-sm text-ink/50 dark:text-paper/50">
          Open source. Bring your own AI.{" "}
          <GithubLink
            iconClassName="mr-1.5 inline-block h-3.5 w-3.5 align-[-0.15em]"
            className="ml-1.5 font-medium text-ink/70 underline-offset-4 hover:text-ink hover:underline dark:text-paper/70 dark:hover:text-paper"
          >
            Self-host it free
          </GithubLink>
        </p>

        <p className="mt-12 text-xs text-ink/50 dark:text-paper/50">
          Runs on your models: local Ollama, OpenAI, or Anthropic, your keys, your call.
        </p>
      </div>
    </section>
  );
}
