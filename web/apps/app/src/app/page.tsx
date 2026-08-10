"use client";

import { BrandMark, Button, Card, ErrorMessage, Spinner, ThemeToggle } from "@bower/ui";
import { logout, webRoutes } from "@bower/api";
import { useRequireAuth } from "@bower/auth";

/** The signed-in landing. Phase 1 proves the whole loop (guarded shell,
 * session, sign out); the product surfaces land in later phases. */
export default function Home() {
  const { user, loading, error } = useRequireAuth();

  async function handleSignOut() {
    const idpLogoutUrl = await logout();
    // Deliberately NO local state clearing before navigating: clearing
    // flips the auth guard to logged-out and its login redirect RACES
    // this navigation; the still-alive IdP session would silently sign
    // the user back in. The full-page navigation resets client state.
    window.location.href = idpLogoutUrl ?? webRoutes.home;
  }

  if (error && !user) {
    return (
      <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
        <div className="max-w-sm space-y-4 text-center">
          <ErrorMessage message="Could not reach the server. Check your connection and try again." />
          <Button onClick={() => window.location.reload()}>Retry</Button>
        </div>
      </main>
    );
  }

  if (loading || !user) {
    return (
      <main className="grid min-h-dvh place-items-center bg-paper-soft dark:bg-ink">
        <Spinner className="h-6 w-6" />
      </main>
    );
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-paper-soft p-6 dark:bg-ink">
      <Card className="w-full max-w-md space-y-6 p-8">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-lg font-bold text-ink dark:text-paper">
            <BrandMark />
            OpenBower
          </span>
          <ThemeToggle />
        </div>
        <div className="space-y-1">
          <p className="text-sm text-ink/60 dark:text-paper/60">Signed in as</p>
          <p className="truncate text-base font-medium text-ink dark:text-paper">{user.email}</p>
        </div>
        <p className="text-sm text-ink/60 dark:text-paper/60">
          The workspace arrives with the next phases; this screen proves the
          session end to end.
        </p>
        <Button variant="outline" onClick={() => void handleSignOut()} className="w-full">
          Sign out
        </Button>
      </Card>
    </main>
  );
}
