import Link from "next/link";
import { BrandMark } from "@bower/ui";

import { isLoggedIn } from "../_lib/auth";
import { APP_URL, DOCS_URL } from "../_lib/urls";
import { GithubLink } from "./GithubLink";
import { MobileMenu } from "./MobileMenu";

export async function Navbar() {
  const authed = await isLoggedIn();
  const authLinks = authed ? (
    <a
      href={APP_URL}
      className="rounded-md bg-signal px-4 py-2 text-center text-sm font-semibold text-paper transition-colors hover:bg-signal-600"
    >
      Open app
    </a>
  ) : (
    // Hosted is not open, so a visitor's one action is the waitlist:
    // an in-page anchor to the CTA's form, never a signup they cannot
    // complete.
    <a
      href="#waitlist"
      className="rounded-md bg-signal px-4 py-2 text-center text-sm font-semibold text-paper transition-colors hover:bg-signal-600"
    >
      Join the waitlist
    </a>
  );

  return (
    <nav className="sticky top-0 z-40 border-b border-ink/10 bg-paper/80 backdrop-blur dark:border-paper/10 dark:bg-ink/80">
      <div className="relative mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2">
          <BrandMark className="h-6 w-6" />
          <span className="text-base font-semibold text-ink dark:text-paper">OpenBower</span>
        </Link>
        <div className="flex items-center gap-2 md:gap-4">
          <div className="hidden items-center gap-6 md:flex">
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="text-sm font-medium text-ink/60 transition-colors hover:text-ink dark:text-paper/60 dark:hover:text-paper"
            >
              Docs
            </a>
            {authLinks}
          </div>
          {/* Stays top-level at every width: icon-only until lg, label
              when there is room. */}
          <GithubLink
            aria-label="OpenBower on GitHub"
            className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium text-ink/60 transition-colors hover:bg-ink/5 hover:text-ink dark:text-paper/60 dark:hover:bg-paper/10 dark:hover:text-paper"
          >
            <span className="hidden lg:inline">GitHub</span>
          </GithubLink>
          <MobileMenu docsUrl={DOCS_URL} authLinks={authLinks} />
        </div>
      </div>
    </nav>
  );
}
