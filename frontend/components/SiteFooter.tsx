import Link from "next/link";
import { LogoMark } from "./Logo";

const DATASETS = [
  ["HPD housing-code violations", "https://data.cityofnewyork.us/d/wvxf-dwi5"],
  ["HPD complaints and problems", "https://data.cityofnewyork.us/d/ygpa-z7cr"],
  ["DOB complaints received", "https://data.cityofnewyork.us/d/eabe-havv"],
  ["DOB ECB violations", "https://data.cityofnewyork.us/d/6bgk-3dad"],
  ["311 service requests", "https://data.cityofnewyork.us/d/erm2-nwe9"],
];

const COLS: { title: string; links: [string, string][] }[] = [
  {
    title: "Product",
    links: [
      ["Research a building", "/#search"],
      ["How it works", "/how-it-works"],
      ["Attention methodology", "/methodology"],
      ["Glossary", "/how-it-works#glossary"],
    ],
  },
  {
    title: "Trust",
    links: [
      ["Report incorrect data", "/corrections"],
      ["Privacy notice", "/privacy"],
      ["Terms and disclaimer", "/terms"],
      ["Source code", "https://github.com/Sjaisw12/nyc-apartment-research-agent"],
    ],
  },
];

export function SiteFooter() {
  return (
    <footer className="no-print mt-24 bg-navy text-slate-300">
      <div className="container-site grid gap-10 py-14 md:grid-cols-[1.5fr_1fr_1fr_1.2fr]">
        <div className="max-w-sm">
          <div className="flex items-center gap-2.5">
            <LogoMark className="h-8 w-8 ring-1 ring-white/15" />
            <span className="font-display text-2xl font-semibold text-white">Stoop</span>
          </div>
          <p className="mt-4 text-sm leading-relaxed">
            Plain-English research on NYC apartment buildings, built from public city records.
          </p>
          <p className="mt-4 text-xs leading-relaxed text-slate-400">
            Stoop is an independent project and is not affiliated with the City of New York or any of its agencies. It is
            not an inspection, legal advice or a prediction of building conditions. Confirm anything important with the
            agency and the landlord.
          </p>
        </div>
        {COLS.map((c) => (
          <div key={c.title}>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">{c.title}</p>
            <ul className="mt-4 space-y-2.5 text-sm">
              {c.links.map(([l, u]) => (
                <li key={u}>
                  {u.startsWith("http") ? (
                    <a href={u} target="_blank" rel="noreferrer" className="hover:text-white">
                      {l}
                    </a>
                  ) : (
                    <Link href={u} className="hover:text-white">
                      {l}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Official data</p>
          <ul className="mt-4 space-y-2.5 text-sm">
            {DATASETS.map(([l, u]) => (
              <li key={u}>
                <a href={u} target="_blank" rel="noreferrer" className="hover:text-white">
                  {l}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <div className="border-t border-white/10">
        <div className="container-site flex flex-col gap-2 py-5 text-xs text-slate-400 sm:flex-row sm:items-center sm:justify-between">
          <span>Data: City of New York via NYC Open Data and NYC Planning GeoSearch. Not an official city service.</span>
          <span>MIT-licensed portfolio project.</span>
        </div>
      </div>
    </footer>
  );
}
