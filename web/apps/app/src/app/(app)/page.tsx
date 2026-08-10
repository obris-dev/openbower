import { Card } from "@bower/ui";

/** The home landing. A placeholder until the lists phase builds the
 * real directory here. */
export default function Home() {
  return (
    <div className="p-6">
      <div className="w-full max-w-4xl space-y-6 pt-2">
        <div>
          <h1 className="text-2xl font-bold text-ink dark:text-paper">Home</h1>
          <p className="mt-1 text-sm text-ink/60 dark:text-paper/60">Your workspace.</p>
        </div>
        <Card className="p-6">
          <p className="text-sm text-ink/60 dark:text-paper/60">
            Nothing here yet. The workspace surfaces arrive with the next
            phases; you are signed in and the shell is live.
          </p>
        </Card>
      </div>
    </div>
  );
}
