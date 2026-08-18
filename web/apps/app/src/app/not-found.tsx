import { Button, PageState } from "@bower/ui";
import { webRoutes } from "@bower/api";

/** Unmatched URLs OUTSIDE the app shell (the (app) group has its own,
 * rendered inside the chrome). */
export default function RootNotFound() {
  return (
    <PageState title="Page not found" subtitle="It may have been deleted, or the link is wrong.">
      <Button size="sm" href={webRoutes.home}>
        Back to Home
      </Button>
    </PageState>
  );
}
