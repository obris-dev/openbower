"use client";

import type { ReactNode } from "react";
import { BowerThemeProvider } from "@bower/ui";

export function Providers({ children }: { children: ReactNode }) {
  return <BowerThemeProvider>{children}</BowerThemeProvider>;
}
