import type { ReactNode } from "react";
import { Card, ThemeToggle } from "@bower/ui";

/** The signed-out shell: a centered brand card with the theme toggle,
 * shared by every screen in this group. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      <Card className="w-full max-w-sm p-8">
        <h1 className="text-xl font-bold text-ink dark:text-paper">OpenBower</h1>
        {children}
      </Card>
    </main>
  );
}
