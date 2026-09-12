import type { Metadata, Viewport } from "next";

import "./globals.css";

import { Providers } from "@/components/layout/providers";

export const metadata: Metadata = {
  title: {
    default: "aiper",
    template: "%s · aiper",
  },
  description:
    "Agentic document generation and compliance analysis for the space sector.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbfa" },
    { media: "(prefers-color-scheme: dark)", color: "#0f0f10" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
