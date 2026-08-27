import "./globals.css";

import type { ReactNode } from "react";
import { Geist_Mono, Poppins } from "next/font/google";
import { ThemeHeadScript, ThemeToggle } from "@bower/ui";

import { Providers } from "./providers";

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
  display: "swap",
});
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata = {
  title: { template: "%s - OpenBower", default: "OpenBower" },
  description:
    "Find the companies that look like your best customers. Open-source prospecting: look-alike discovery, lead lists, and agents that fill in the blanks.",
  metadataBase: new URL("https://openbower.ai"),
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${poppins.variable} ${geistMono.variable} min-h-dvh`} suppressHydrationWarning>
      <body className="flex min-h-dvh flex-col font-sans antialiased">
        {/* Blocking script applies the theme before paint (no flash). */}
        <ThemeHeadScript />
        <Providers>
          {children}
          <div className="fixed bottom-6 right-6 z-50 rounded-full border border-ink/10 bg-paper shadow-lg dark:border-paper/15 dark:bg-ink">
            <ThemeToggle />
          </div>
        </Providers>
      </body>
    </html>
  );
}
