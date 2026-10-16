import Link from "next/link";

import { DOCS_URL, GITHUB_URL } from "../_lib/urls";
import { GithubLink } from "./GithubLink";

/** Two groups, which is the information: the fine print (who holds the
 * copyright, the licence that qualifies it, the two legal pages) on the
 * left in the quiet tone, and the places to go next (docs, the code) on
 * the right. On a phone the groups
 * stack, links first and fine print last, in their order of importance
 * rather than their DOM order. No "all rights reserved": the software is
 * Apache 2.0 and the line would say otherwise. */
export function Footer() {
  const quiet = "transition-colors hover:text-ink dark:hover:text-paper";
  return (
    <footer className="mt-auto border-t border-ink/10 dark:border-paper/10">
      <div className="mx-auto flex w-full max-w-6xl flex-col-reverse gap-y-4 px-6 py-8 text-sm text-ink/50 dark:text-paper/50 sm:flex-row sm:items-center sm:justify-between sm:gap-x-8">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
          <p>&copy; {new Date().getFullYear()} Obris LLC</p>
          <a href={`${GITHUB_URL}/blob/main/LICENSE`} target="_blank" rel="noreferrer noopener" className={quiet}>
            Apache 2.0
          </a>
          <Link href="/privacy" className={quiet}>
            Privacy
          </Link>
          <Link href="/terms" className={quiet}>
            Terms
          </Link>
        </div>
        <div className="flex gap-6">
          <a href={DOCS_URL} target="_blank" rel="noreferrer noopener" className={quiet}>
            Docs
          </a>
          <GithubLink className={`inline-flex items-center gap-1.5 ${quiet}`}>GitHub</GithubLink>
        </div>
      </div>
    </footer>
  );
}
