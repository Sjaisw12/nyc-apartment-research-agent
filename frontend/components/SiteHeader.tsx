"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { LogoMark } from "./Logo";

const NAV = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/methodology", label: "Methodology" },
  { href: "/#examples", label: "Examples" },
  { href: "https://github.com/Sjaisw12/nyc-apartment-research-agent", label: "GitHub", external: true },
];

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  return (
    <header className="no-print sticky top-0 z-40 border-b border-line bg-white/90 backdrop-blur-md supports-[backdrop-filter]:bg-white/80">
      <div className="container-site flex h-16 items-center justify-between gap-6">
        <Link href="/" className="flex items-center gap-2.5" aria-label="Stoop home">
          <LogoMark />
          <span className="leading-none">
            <span className="block font-display text-[24px] font-semibold tracking-tight text-navy">Stoop</span>
            <span className="mt-1 hidden text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-faint sm:block">
              Building intelligence · NYC
            </span>
          </span>
        </Link>
        <nav className="hidden items-center gap-7 text-sm font-medium text-ink-soft md:flex" aria-label="Main">
          {NAV.map((n) =>
            n.external ? (
              <a key={n.href} href={n.href} target="_blank" rel="noreferrer" className="hover:text-ink">
                {n.label}
              </a>
            ) : (
              <Link key={n.href} href={n.href} className="hover:text-ink">
                {n.label}
              </Link>
            ),
          )}
          <Link href="/#search" className="btn-primary px-4 py-2">
            Research a building
          </Link>
        </nav>
        <button
          type="button"
          className="grid h-10 w-10 place-items-center rounded-xl border border-line bg-white md:hidden"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </div>
      {open && (
        <nav id="mobile-nav" className="border-t border-line bg-white md:hidden" aria-label="Mobile">
          <div className="container-site flex flex-col py-3 text-base font-medium">
            {NAV.map((n) =>
              n.external ? (
                <a key={n.href} href={n.href} target="_blank" rel="noreferrer" className="rounded-lg px-2 py-3 hover:bg-white">
                  {n.label}
                </a>
              ) : (
                <Link key={n.href} href={n.href} className="rounded-lg px-2 py-3 hover:bg-white" onClick={() => setOpen(false)}>
                  {n.label}
                </Link>
              ),
            )}
            <Link href="/#search" className="btn-primary mt-2 py-3" onClick={() => setOpen(false)}>
              Research a building
            </Link>
          </div>
        </nav>
      )}
    </header>
  );
}
