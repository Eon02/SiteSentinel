import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import AlertsProvider from "@/components/AlertsProvider";
import AlertBanner from "@/components/AlertBanner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SiteSentinel",
  description: "Job site hazard ranking, live safety alerts, and crew hazard reports.",
};

export const viewport: Viewport = {
  themeColor: "#16213E",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      {/* Desktop: viewport-height shell so the dashboard map and list fill the screen; main scrolls. */}
      <body className="flex min-h-full flex-col lg:h-dvh">
        <AlertsProvider>
          <header className="z-20 shrink-0 bg-[#16213E] text-white shadow">
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2">
              <Link href="/" className="flex min-h-11 items-center text-xl font-extrabold tracking-tight">
                Site<span className="text-[#E8590C]">Sentinel</span>
              </Link>
              <nav aria-label="Main" className="flex items-center gap-2">
                <Link href="/" className="flex min-h-11 items-center rounded-lg px-3 font-semibold hover:bg-white/10">
                  Dashboard
                </Link>
                <Link
                  href="/report"
                  className="flex min-h-11 items-center rounded-lg bg-[#E8590C] px-3 font-bold text-white hover:bg-[#C2410C]"
                >
                  Report Hazard
                </Link>
              </nav>
            </div>
            <AlertBanner />
          </header>
          <main className="flex flex-1 flex-col lg:min-h-0 lg:overflow-y-auto">{children}</main>
        </AlertsProvider>
      </body>
    </html>
  );
}
