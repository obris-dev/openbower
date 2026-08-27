import { DOCS_URL } from "../_lib/urls";
import { GithubLink } from "./GithubLink";

export function Footer() {
  return (
    <footer className="mt-auto border-t border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-6 py-8 text-sm text-ink/50 dark:text-paper/50">
        <p>&copy; {new Date().getFullYear()} OpenBower. All rights reserved.</p>
        <div className="flex gap-6">
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer noopener"
            className="transition-colors hover:text-ink dark:hover:text-paper"
          >
            Docs
          </a>
          <GithubLink className="inline-flex items-center gap-1.5 transition-colors hover:text-ink dark:hover:text-paper">
            GitHub
          </GithubLink>
        </div>
      </div>
    </footer>
  );
}
