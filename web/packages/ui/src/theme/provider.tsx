"use client";

import { ThemeProvider, useTheme } from "next-themes";
import { useEffect, type ReactNode } from "react";

import { THEME, THEME_COOKIE_NAME, THEME_STORAGE_KEY, type Theme } from "./constants";


// The cookie is the CROSS-SITE source of truth for an explicit choice
// (sibling apps share it; localStorage is per-origin). The head script
// honors it pre-paint, but after hydration next-themes re-applies its
// own localStorage, which on a sibling origin is empty or stale and
// would override the shared choice, so the cookie is synced back into
// next-themes on mount and whenever the tab regains focus (picking up
// a toggle made on a sibling site).
function ThemeCookieSync() {
  const { theme, setTheme } = useTheme();
  useEffect(() => {
    function sync() {
      const match = document.cookie.match(new RegExp(`(?:^|; )${THEME_COOKIE_NAME}=([^;]+)`));
      const fromCookie = match ? match[1] : null;
      if ((fromCookie === THEME.LIGHT || fromCookie === THEME.DARK) && fromCookie !== theme) {
        setTheme(fromCookie);
      }
    }
    sync();
    window.addEventListener("focus", sync);
    return () => window.removeEventListener("focus", sync);
  }, [theme, setTheme]);
  return null;
}

// Wraps next-themes with the class strategy (globals.css maps `.dark`), and
// persists the choice to a cookie as well as localStorage so a future
// server render / sibling app on the same registrable domain can read it.
export function BowerThemeProvider({
  children,
  defaultTheme = THEME.SYSTEM,
}: {
  children: ReactNode;
  defaultTheme?: Theme;
}) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme={defaultTheme}
      enableSystem
      storageKey={THEME_STORAGE_KEY}
      disableTransitionOnChange
    >
      <ThemeCookieSync />
      {children}
    </ThemeProvider>
  );
}

// Persist an explicit theme choice to the cookie. Host-only by default (works
// on any host, including a self-hosted deployment); set NEXT_PUBLIC_COOKIE_DOMAIN
// to a registrable domain (e.g. ".openbower.ai") only when you want sibling
// apps on that domain to share the choice. A hardcoded domain would make the
// browser reject the cookie on any other host, so the theme silently would not
// persist for self-hosters.
export function setThemeCookie(theme: Theme): void {
  const configuredDomain = process.env.NEXT_PUBLIC_COOKIE_DOMAIN;
  const domain = configuredDomain ? `; domain=${configuredDomain}; Secure` : "";
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${THEME_COOKIE_NAME}=${theme}; path=/; max-age=${oneYear}; SameSite=Lax${domain}`;
}
