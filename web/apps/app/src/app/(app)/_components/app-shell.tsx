"use client";

import { usePathname } from "next/navigation";
import { Compass, House } from "lucide-react";
import { Sidebar, ThemeToggle } from "@bower/ui";
import { logout, webRoutes } from "@bower/api";
import { useUser } from "@bower/auth";

/** The signed-in chrome: sidebar nav + header. Nav items land with the
 * phases that build their screens. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user } = useUser();

  const navigation = [
    { name: "Home", href: webRoutes.home, icon: House, current: pathname === webRoutes.home },
    { name: "Discover", href: webRoutes.discover, icon: Compass, current: pathname.startsWith(webRoutes.discover) },
  ];

  async function handleSignOut() {
    const idpLogoutUrl = await logout();
    // Deliberately NO local state clearing before navigating: clearing
    // flips the auth guard to logged-out and its login redirect RACES
    // this navigation; the still-alive IdP session would silently sign
    // the user back in. The full-page navigation resets client state.
    window.location.href = idpLogoutUrl ?? webRoutes.home;
  }

  return (
    <Sidebar
      navigation={navigation}
      userNavigation={[{ name: "Sign out", onClick: () => void handleSignOut() }]}
      account={{ email: user?.email }}
      homeHref={webRoutes.home}
      headerActions={<ThemeToggle />}
    >
      {children}
    </Sidebar>
  );
}
