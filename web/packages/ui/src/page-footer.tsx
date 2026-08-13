import type { ReactNode } from "react";
import { cn } from "./cn";

/** The pinned page footer: a page's primary actions in a fixed bar over
 * the content (offset past the sidebar on desktop), one shared look for
 * every screen with a single main flow. */
export function PageFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <footer className="fixed bottom-0 left-0 right-0 z-20 border-t border-hairline bg-surface/90 backdrop-blur lg:left-[var(--sidebar-w)]">
      <div className={cn("mx-auto flex w-full max-w-7xl flex-wrap items-center justify-end gap-2 px-6 py-3", className)}>
        {children}
      </div>
    </footer>
  );
}
