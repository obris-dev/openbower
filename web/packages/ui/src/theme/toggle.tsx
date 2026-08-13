"use client";

import { Circle, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { setThemeCookie } from "./provider";
import { THEME } from "./constants";

// "Have we hydrated yet?" without a mount effect + setState (which would
// cascade a render). useSyncExternalStore returns the SERVER snapshot (false)
// for SSR and the first client render so they match, then the client snapshot
// (true) after hydration. The store never changes, so subscribe is a no-op.
const noopSubscribe = () => () => {};
const useHydrated = () =>
  useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );

// Sun/moon toggle. Renders a neutral placeholder until mounted (so SSR and
// first client render match, no hydration mismatch), then the Lucide icon for
// the resolved theme. Writes both next-themes state and the cookie.
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useHydrated();

  function toggle() {
    const next = resolvedTheme === THEME.DARK ? THEME.LIGHT : THEME.DARK;
    setTheme(next);
    setThemeCookie(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle theme"
      className="rounded-md p-2 text-muted hover:bg-wash"
    >
      {/* Avoid rendering a theme-specific icon until mounted to dodge SSR
          mismatch; Circle is the neutral pre-hydration placeholder. */}
      {mounted ? (
        resolvedTheme === THEME.DARK ? (
          <Sun aria-hidden className="h-4 w-4" />
        ) : (
          <Moon aria-hidden className="h-4 w-4" />
        )
      ) : (
        <Circle aria-hidden className="h-4 w-4" />
      )}
    </button>
  );
}
