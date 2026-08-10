"use client";

import { useState, type ComponentType, type ReactNode } from "react";
import { ChevronDown, CircleUser, Menu, PanelLeft, X } from "lucide-react";
import { cn } from "../cn";
import { BrandMark } from "../brand-mark";
import { Dropdown, DropdownButton, DropdownItem, DropdownMenu } from "../dropdown";
import { SIDEBAR_COLLAPSE_KEY } from "./constants";

/** The app shell: fixed dark sidebar (nav) + sticky top header (actions +
 * profile menu) + content area. A plain-React port of the reference shell
 * (mobile drawer and profile dropdown are simple state + backdrop, no
 * dialog library). Navigation/userNavigation are data, so screens stay
 * declarative. */

export type SidebarNavItem = {
  name: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
  current?: boolean;
};

export type SidebarUserNavItem = {
  name: string;
  href?: string;
  onClick?: () => void;
};

function NavItems({
  navigation,
  collapsible = false,
  onNavigate,
}: {
  navigation: SidebarNavItem[];
  collapsible?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <ul role="list" className="-mx-2 space-y-1">
      {navigation.map((item) => (
        <li key={item.name}>
          <a
            href={item.href}
            onClick={onNavigate}
            title={collapsible ? item.name : undefined}
            className={cn(
              item.current ? "bg-paper/10 text-paper" : "text-paper/60 hover:bg-paper/10 hover:text-paper",
              "flex items-center gap-x-3 rounded-md p-2 text-sm font-semibold",
              collapsible && "[[data-sidebar=collapsed]_&]:justify-center",
            )}
          >
            <item.icon aria-hidden className="h-5 w-5 shrink-0" />
            {collapsible ? <span className="[[data-sidebar=collapsed]_&]:hidden">{item.name}</span> : item.name}
          </a>
        </li>
      ))}
    </ul>
  );
}

export function Sidebar({
  children,
  brand = "OpenBower",
  homeHref = "/",
  navigation = [],
  userNavigation = [],
  account = {},
  headerActions,
}: {
  children: ReactNode;
  brand?: string;
  homeHref?: string;
  navigation?: SidebarNavItem[];
  userNavigation?: SidebarUserNavItem[];
  account?: { email?: string };
  headerActions?: ReactNode;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Collapse is NOT React state: SidebarHeadScript stamps data-sidebar on
  // <html> before first paint and every variant difference below is CSS
  // keyed off that attribute, so server HTML (which cannot know the
  // preference) never paints the wrong variant. State here would flash:
  // SSR always renders the default, hydration is after first paint.
  function toggleCollapsed() {
    const root = document.documentElement;
    const wasCollapsed = root.getAttribute("data-sidebar") === "collapsed";
    if (wasCollapsed) root.removeAttribute("data-sidebar");
    else root.setAttribute("data-sidebar", "collapsed");
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSE_KEY, wasCollapsed ? "0" : "1");
    } catch {
      // Preference just doesn't persist (private mode etc.).
    }
  }

  const brandMark = (
    <a href={homeHref} className="flex min-w-0 items-center gap-2 text-lg font-bold text-paper">
      <BrandMark />
      <span className="truncate">{brand}</span>
    </a>
  );

  return (
    // Content and any fixed page chrome (e.g. an action footer) share the
    // sidebar's width through --sidebar-w, so the reflow is one number:
    // push, never overlay.
    <div className="min-h-dvh bg-paper-soft [--sidebar-w:18rem] dark:bg-ink [[data-sidebar=collapsed]_&]:[--sidebar-w:4rem]">
      {/* Mobile drawer */}
      {drawerOpen && (
        <div className="relative z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close sidebar"
            onClick={() => setDrawerOpen(false)}
            className="fixed inset-0 bg-ink/80"
          />
          <div className="fixed inset-y-0 left-0 flex w-72 flex-col gap-y-5 overflow-y-auto bg-ink px-6 pb-4 ring-1 ring-paper/10">
            <div className="flex h-16 shrink-0 items-center justify-between">
              {brandMark}
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                className="p-2 text-paper/70 hover:text-paper"
              >
                <span className="sr-only">Close sidebar</span>
                <X aria-hidden className="h-5 w-5" />
              </button>
            </div>
            <nav className="flex flex-1 flex-col">
              <NavItems navigation={navigation} onNavigate={() => setDrawerOpen(false)} />
            </nav>
          </div>
        </div>
      )}

      {/* Desktop sidebar (dark in both themes, like the reference):
          icon rail when collapsed, full when expanded; content reflows. */}
      <div className="hidden transition-[width] duration-200 lg:fixed lg:inset-y-0 lg:z-40 lg:flex lg:w-[var(--sidebar-w)] lg:flex-col">
        <div className="flex grow flex-col gap-y-5 overflow-y-auto bg-ink px-6 pb-4 dark:border-r dark:border-paper/10 [[data-sidebar=collapsed]_&]:px-2">
          {/* Rail top, the reference affordance: the monogram at rest,
              the panel icon on hover; one click expands. Both tops are in
              the DOM; the html attribute picks one. */}
          <button
            type="button"
            onClick={toggleCollapsed}
            title="Expand sidebar"
            className="group/rail hidden h-16 shrink-0 items-center justify-center [[data-sidebar=collapsed]_&]:flex"
          >
            <span className="group-hover/rail:hidden">
              <BrandMark />
            </span>
            <PanelLeft aria-hidden className="hidden h-5 w-5 text-paper/80 group-hover/rail:block" />
          </button>
          <div className="flex h-16 shrink-0 items-center justify-between [[data-sidebar=collapsed]_&]:hidden">
            {brandMark}
            <button
              type="button"
              onClick={toggleCollapsed}
              title="Collapse sidebar"
              className="rounded-md p-1.5 text-paper/50 hover:bg-paper/10 hover:text-paper"
            >
              <PanelLeft aria-hidden className="h-5 w-5" />
            </button>
          </div>
          <nav className="flex flex-1 flex-col">
            <NavItems navigation={navigation} collapsible />
          </nav>
        </div>
      </div>

      {/* Header + content */}
      <div className="transition-[padding] duration-200 lg:pl-[var(--sidebar-w)]">
        <div className="sticky top-0 z-30 flex h-16 shrink-0 items-center gap-x-4 border-b border-ink/10 bg-paper px-4 sm:px-6 lg:px-8 dark:border-paper/10 dark:bg-ink">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="-m-2.5 p-2.5 text-ink lg:hidden dark:text-paper"
          >
            <span className="sr-only">Open sidebar</span>
            <Menu aria-hidden className="h-5 w-5" />
          </button>
          <div aria-hidden className="h-6 w-px bg-ink/10 lg:hidden dark:bg-paper/10" />

          <div className="flex flex-1 items-center justify-end gap-x-4 lg:gap-x-6">
            {headerActions}
            <div aria-hidden className="hidden lg:block lg:h-6 lg:w-px lg:bg-ink/10 dark:lg:bg-paper/10" />

            {/* Profile dropdown (the behavior layer owns focus + dismiss) */}
            <Dropdown>
              <DropdownButton className="flex items-center gap-x-1 rounded-lg p-1.5 hover:bg-ink/5 dark:hover:bg-paper/10">
                <span className="sr-only">Open user menu</span>
                <CircleUser aria-hidden className="h-6 w-6 text-ink/70 dark:text-paper/70" />
                <ChevronDown aria-hidden className="h-4 w-4 text-ink/50 dark:text-paper/50" />
              </DropdownButton>
              <DropdownMenu anchor="bottom end" className="w-64 py-0">
                {account.email && (
                  <div className="flex items-center gap-x-3 border-b border-ink/10 px-4 py-3 dark:border-paper/10">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-signal text-sm font-medium text-signal-900">
                      {account.email.charAt(0).toUpperCase()}
                    </span>
                    <span className="truncate text-sm text-ink/80 dark:text-paper/80">{account.email}</span>
                  </div>
                )}
                {userNavigation.map((item) =>
                  item.onClick ? (
                    <DropdownItem key={item.name} onClick={() => item.onClick?.()}>
                      {item.name}
                    </DropdownItem>
                  ) : (
                    <DropdownItem key={item.name} href={item.href ?? "#"}>
                      {item.name}
                    </DropdownItem>
                  ),
                )}
              </DropdownMenu>
            </Dropdown>
          </div>
        </div>

        <main className="min-h-[calc(100dvh-4rem)]">{children}</main>
      </div>
    </div>
  );
}
