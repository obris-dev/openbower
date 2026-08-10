"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { webRoutes } from "@bower/api";
import { useUser } from "@bower/auth";

/** Signed-in visitors have no business on the credential screens; send
 * them home. Renders nothing. In-app transition, so the router (per the
 * navigation rule); replace, not push, so Back doesn't return them to a
 * login form. Waits for the check (no bounce on loading), and an error
 * result stays put: the form must remain usable when /me is down. */
export function RedirectIfAuthed() {
  const { user, loading } = useUser();
  const router = useRouter();

  useEffect(() => {
    if (!loading && user !== null) router.replace(webRoutes.home);
  }, [loading, user, router]);

  return null;
}
