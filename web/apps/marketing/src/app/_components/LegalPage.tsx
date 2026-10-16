import type { ReactNode } from "react";

import { Footer } from "./Footer";
import { Navbar } from "./Navbar";

/** The shell the two legal pages share: the site chrome around a
 * narrow reading measure. Sections are plain h2 + paragraphs so the
 * text, not the layout, carries the page. */
export function LegalPage({ title, effective, children }: { title: string; effective: string; children: ReactNode }) {
  return (
    <>
      <Navbar />
      <main className="mx-auto w-full max-w-3xl px-6 py-24">
        <h1 className="text-3xl font-bold text-ink dark:text-paper">{title}</h1>
        <p className="mt-3 text-sm text-ink/60 dark:text-paper/60">Effective {effective}</p>
        <div className="legal mt-10 space-y-5 text-base leading-7 text-ink/80 dark:text-paper/80">{children}</div>
      </main>
      <Footer />
    </>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-4 pt-4">
      <h2 className="text-xl font-semibold text-ink dark:text-paper">{title}</h2>
      {children}
    </section>
  );
}

export function Mail({ address }: { address: string }) {
  return (
    <a href={`mailto:${address}`} className="text-signal hover:underline">
      {address}
    </a>
  );
}
