import { PageState } from "@bower/ui";
import { webRoutes } from "@bower/api";

import { LinkButton } from "./_components/link-button";

/** Unmatched URLs OUTSIDE the app shell (the (app) group has its own,
 * rendered inside the chrome). */
export default function RootNotFound() {
  return (
    <PageState title="Page not found" subtitle="It may have been deleted, or the link is wrong.">
      <LinkButton size="sm" href={webRoutes.home}>
        Back to Home
      </LinkButton>
    </PageState>
  );
}
