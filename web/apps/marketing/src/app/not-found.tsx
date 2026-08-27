import Link from "next/link";

import { Footer } from "./_components/Footer";
import { Navbar } from "./_components/Navbar";

export default function NotFound() {
  return (
    <>
      <Navbar />
      <main className="mx-auto w-full max-w-6xl px-6 py-32 text-center">
        <h1 className="text-3xl font-bold text-ink dark:text-paper">Page not found</h1>
        <p className="mt-3 text-sm text-ink/60 dark:text-paper/60">
          <Link href="/" className="text-signal hover:underline">
            Back to openbower.ai
          </Link>
        </p>
      </main>
      <Footer />
    </>
  );
}
