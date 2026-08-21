import { PageState } from "@bower/ui";
import { webRoutes } from "@bower/api";

import { LinkButton } from "../_components/link-button";

/** notFound() inside the app shell: styled, with a way home. */
export default function AppNotFound() {
  return (
    <PageState title="Page not found" subtitle="It may have been deleted, or the link is wrong.">
      <LinkButton size="sm" href={webRoutes.home}>
        Back to Home
      </LinkButton>
    </PageState>
  );
}
