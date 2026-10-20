"use client";

import { usePathname, useRouter } from "next/navigation";
import { Bot, Compass, House } from "lucide-react";
import { Sidebar, ThemeToggle } from "@bower/ui";
import { webRoutes } from "@bower/api";
import { useUser } from "@bower/auth";

import { signOut } from "./sign-out";

/** The signed-in chrome: sidebar nav + header. Nav items land with the
 * phases that build their screens. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useUser();

  const navigation = [
    {
      name: "Home",
      href: webRoutes.home,
      icon: House,
      current: pathname === webRoutes.home || pathname.startsWith(webRoutes.listPrefix),
    },
    { name: "Discover", href: webRoutes.discover, icon: Compass, current: pathname.startsWith(webRoutes.discover) },
    { name: "Agents", href: webRoutes.agents, icon: Bot, current: pathname.startsWith(webRoutes.agents) },
  ];

  return (
    <Sidebar
      navigation={navigation}
      userNavigation={[
        // An in-app transition rides the router (the primitive's href
        // form is a full document load, for boundary crossings).
        { name: "Settings", onClick: () => router.push(webRoutes.settings) },
        { name: "Sign out", onClick: () => void signOut() },
      ]}
      account={{ email: user?.email }}
      homeHref={webRoutes.home}
      headerActions={<ThemeToggle />}
    >
      {children}
    </Sidebar>
  );
}
