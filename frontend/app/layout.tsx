import type { Metadata, Viewport } from "next";
import { Inter, Source_Serif_4 } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const display = Source_Serif_4({ subsets: ["latin"], variable: "--font-display", display: "swap" });

export const metadata: Metadata = {
  title: "Stoop · NYC Apartment Research",
  description:
    "Look up an NYC building before you sign a lease. Official HPD, DOB, ECB and 311 records, organized into a cited report that separates findings from allegations.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1f5f5b" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${display.variable}`}>
      <body className="min-h-screen font-sans">
        <header className="no-print border-b border-stone-200/70 bg-paper/80 backdrop-blur supports-[backdrop-filter]:bg-paper/70">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2" aria-label="Stoop home">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-white">
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M4 20V9l8-5 8 5v11" />
                  <path d="M9 20v-6h6v6" />
                </svg>
              </span>
              <span className="font-display text-xl font-semibold tracking-tight">Stoop</span>
              <span className="hidden text-sm text-ink-faint sm:inline">NYC apartment research</span>
            </Link>
            <nav className="flex items-center gap-4 text-sm text-ink-soft">
              <Link href="/#how" className="hover:text-ink">How it works</Link>
              <a
                href="https://github.com/Sjaisw12/nyc-apartment-research-agent"
                className="hover:text-ink"
                target="_blank"
                rel="noreferrer"
              >
                GitHub
              </a>
            </nav>
          </div>
        </header>
        <main>{children}</main>
        <footer className="no-print mt-20 border-t border-stone-200/70">
          <div className="mx-auto max-w-6xl px-4 py-8 text-xs leading-relaxed text-ink-faint sm:px-6">
            Stoop summarizes public NYC Open Data. It is not legal advice, not an inspection, and not
            a judgment that an apartment is safe or unsafe to rent. Records can be incomplete or
            delayed; confirm anything important with the agency and the landlord.
          </div>
        </footer>
      </body>
    </html>
  );
}
