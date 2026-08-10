"use client";

import type { ReactNode } from "react";
import { BowerThemeProvider, ToastProvider } from "@bower/ui";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <BowerThemeProvider>
      <ToastProvider>{children}</ToastProvider>
    </BowerThemeProvider>
  );
}
