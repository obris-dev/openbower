import { Button, PageState } from "@bower/ui";
import { webRoutes } from "@bower/api";

/** notFound() inside the app shell: styled, with a way home. */
export default function AppNotFound() {
  return (
    <PageState title="Page not found" subtitle="It may have been deleted, or the link is wrong.">
      <Button size="sm" href={webRoutes.home}>
        Back to Home
      </Button>
    </PageState>
  );
}
