import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";
import Sidebar from "@/components/Sidebar";
import "./globals.css";

/* ── Font configuration ──────────────────────────────────────── */

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

/* ── Metadata ────────────────────────────────────────────────── */

export const metadata: Metadata = {
  title: {
    default: "Antigravity — YouTube Shorts Factory",
    template: "%s | Antigravity",
  },
  description:
    "Automated YouTube Shorts clipping, AI story generation, and multi-channel publishing pipeline.",
  keywords: [
    "YouTube Shorts",
    "automation",
    "AI video",
    "content factory",
    "Antigravity",
  ],
};

/* ── Root Layout ─────────────────────────────────────────────── */

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${outfit.variable} h-full antialiased`}
    >
      <body className="h-screen bg-bg-base text-text-primary flex overflow-hidden">
        {/* Ambient background glow */}
        <div className="ambient-glow" aria-hidden="true" />

        {/* Sidebar navigation */}
        <Sidebar />

        {/* Main scrollable content area */}
        <main className="flex-1 overflow-y-auto relative z-10">
          <div className="max-w-7xl mx-auto px-6 py-8 lg:px-10">
            {children}
          </div>
        </main>
      </body>
    </html>
  );
}
