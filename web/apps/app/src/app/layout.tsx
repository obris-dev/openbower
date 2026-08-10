import "./globals.css";
import type { ReactNode } from "react";
import { Geist_Mono, Poppins } from "next/font/google";
import { SidebarHeadScript, ThemeHeadScript } from "@bower/ui";

import { Providers } from "./providers";

const poppins = Poppins({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-poppins",
});
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });

export const metadata = { title: "OpenBower", description: "The GTM workspace that's yours." };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${poppins.variable} ${geistMono.variable}`}>
      <body className="font-sans antialiased">
        {/* Blocking scripts apply theme + sidebar state before paint (no flash). */}
        <ThemeHeadScript />
        <SidebarHeadScript />
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
