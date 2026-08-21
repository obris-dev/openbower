"use client";

import { useEffect } from "react";

import { Button, PageState } from "@bower/ui";

/** The auth group's error boundary: a broken login/signup render must
 * offer a retry, not Next's unstyled default (this is the front
 * door). */
export default function AuthError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  // The boundary swallows the render; the console keeps the evidence
  // (and the digest that matches the server log line).
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <PageState title="Sign-in is unavailable" subtitle="The page could not be loaded. Retry in a moment.">
      <Button size="sm" onClick={() => reset()}>
        Try again
      </Button>
    </PageState>
  );
}
