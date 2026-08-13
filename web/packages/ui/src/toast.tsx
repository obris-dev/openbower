"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import * as RadixToast from "@radix-ui/react-toast";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import { cn } from "./cn";

/** Toasts, mirroring the obris notification shape (Radix Toast under a
 * provider + hook): screens report outcomes with one call and never own
 * layout for them. useToast().error/success take the message (and an
 * optional title); the viewport stacks top-right. */

type ToastKind = "error" | "success";
type ToastRecord = { id: number; kind: ToastKind; message: string; title?: string };

type ToastApi = {
  error: (message: string, title?: string) => void;
  success: (message: string, title?: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast must be used within a ToastProvider");
  return api;
}

function ToastItem({ toast, dismiss }: { toast: ToastRecord; dismiss: (id: number) => void }) {
  return (
    <RadixToast.Root
      duration={toast.kind === "error" ? 8000 : 5000}
      onOpenChange={(open) => {
        if (!open) dismiss(toast.id);
      }}
      className={cn(
        "pointer-events-auto w-full overflow-hidden rounded-lg bg-surface p-4 shadow-lg ring-1 ring-hairline",
        "data-[state=open]:animate-toast-in data-[state=closed]:animate-toast-out",
        "data-[swipe=move]:translate-x-[var(--radix-toast-swipe-move-x)] data-[swipe=cancel]:translate-x-0",
        "data-[swipe=cancel]:transition-transform data-[swipe=end]:animate-toast-out",
      )}
    >
      <div className="flex items-start gap-3">
        {toast.kind === "error" ? (
          <CircleAlert aria-hidden className="h-5 w-5 shrink-0 text-red-500" />
        ) : (
          <CircleCheck aria-hidden className="h-5 w-5 shrink-0 text-green-500" />
        )}
        <div className="min-w-0 flex-1">
          {toast.title && (
            <RadixToast.Title className="text-sm font-semibold text-foreground">
              {toast.title}
            </RadixToast.Title>
          )}
          <RadixToast.Description
            className={cn(
              "text-sm [overflow-wrap:anywhere]",
              toast.title ? "mt-0.5 text-muted" : "font-medium text-foreground",
            )}
          >
            {toast.message}
          </RadixToast.Description>
        </div>
        <RadixToast.Close className="shrink-0 rounded p-0.5 text-faint hover:text-foreground">
          <span className="sr-only">Dismiss</span>
          <X aria-hidden className="h-4 w-4" />
        </RadixToast.Close>
      </div>
    </RadixToast.Root>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const counter = useRef(0);

  const push = useCallback((kind: ToastKind, message: string, title?: string) => {
    const id = ++counter.current;
    setToasts((prev) => [...prev, { id, kind, message, title }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      error: (message, title) => push("error", message, title),
      success: (message, title) => push("success", message, title),
    }),
    [push],
  );

  return (
    <RadixToast.Provider swipeDirection="right">
      <ToastContext.Provider value={api}>{children}</ToastContext.Provider>
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} dismiss={dismiss} />
      ))}
      <RadixToast.Viewport className="fixed right-0 top-0 z-[100] flex w-full max-w-sm flex-col gap-2 p-4 outline-none sm:p-6" />
    </RadixToast.Provider>
  );
}
