"use client";

import { useEffect } from "react";

import { Button, PageState } from "@bower/ui";

/** The route group's error boundary: server-render failures land here
 * with a retry, instead of Next's unstyled default. */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  // The boundary swallows the render; the console keeps the evidence
  // (and the digest that matches the server log line).
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <PageState title="Something went wrong" subtitle="The page could not be loaded. Retry in a moment.">
      <Button size="sm" onClick={() => reset()}>
        Try again
      </Button>
    </PageState>
  );
}
