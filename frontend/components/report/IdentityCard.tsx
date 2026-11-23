"use client";

import { BadgeCheck, Building2, CalendarClock, Download, FileJson, FileText, Landmark, Printer, Radio } from "lucide-react";
import type { ResearchReport } from "@/lib/types";
import { fmtDate, latestDataDate } from "@/lib/format";
import { markdownUrl } from "@/lib/api";
import { ExternalLink } from "./ui";
import { Term } from "@/components/Term";

const CONF_CLS: Record<string, string> = {
  high: "bg-white/10 text-white ring-white/25",
  medium: "bg-amber-50 text-amber-800 ring-amber-200",
  needs_choice: "bg-sky-50 text-sky-800 ring-sky-200",
  none: "bg-rose-50 text-rose-800 ring-rose-200",
};

const REG_LABEL: Record<string, string> = {
  on_file_end_date_not_passed: "Registration on file",
  on_file_end_date_passed_after_data_update: "Registration status unknown",
  on_file_end_date_passed_before_data_update: "Registration end date passed in this snapshot",
  on_file_end_date_passed_freshness_unknown: "Registration status unknown",
  on_file_end_date_unknown: "Registration on file, end date unknown",
  none_on_file: "No registration found in this snapshot",
};

function titleCase(label?: string | null) {
  if (!label) return "";
  const [street, ...rest] = label.replace(/, USA$/, "").split(",");
  const tc = street
    .toLowerCase()
    .replace(/\b([a-z])/g, (c) => c.toUpperCase())
    .replace(/\b(\d+)(St|Nd|Rd|Th)\b/g, (_, n, s) => n + s.toLowerCase());
  return { street: tc, rest: rest.join(",").trim() };
}

export function ExportBar({ report }: { report: ResearchReport }) {
  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stoop-report-${report.resolution.bbl ?? "address"}-${report.report_id.slice(0, 8)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <div className="no-print grid grid-cols-3 gap-2 sm:flex sm:flex-wrap" data-testid="export-bar">
      <button type="button" className="btn bg-white px-3 text-navy hover:bg-brand-50 sm:px-5" onClick={() => window.print()} aria-label="Print or save as PDF">
        <Printer className="h-4 w-4" aria-hidden />
        <span>
          Print<span className="hidden sm:inline"> / PDF</span>
        </span>
      </button>
      <a className="btn border border-white/25 px-3 text-white hover:bg-white/10 sm:px-5" href={markdownUrl(report.report_id)} download aria-label="Markdown">
        <FileText className="h-4 w-4" aria-hidden /> Markdown
      </a>
      <button type="button" className="btn border border-white/25 px-3 text-white hover:bg-white/10 sm:px-5" onClick={downloadJson} aria-label="JSON">
        <FileJson className="h-4 w-4" aria-hidden /> JSON
      </button>
    </div>
  );
}

const TERMS: Record<string, string> = { "Tax lot (BBL)": "BBL", "Building (BIN)": "BIN" };

export function IdentityCard({ report }: { report: ResearchReport }) {
  const r = report.resolution;
  const building = report.building;
  const name = titleCase(r.label);
  const asOf = latestDataDate(report.sources.map((s) => s.data_as_of));
  const lotOnly = r.level === "tax_lot";
  const live = report.run.mode === "live";
  const facts: [string, string][] = [];
  if (r.bbl) facts.push(["Tax lot (BBL)", r.bbl]);
  facts.push(["Building (BIN)", r.bin ?? (lotOnly ? "Not verified" : "—")]);
  if (building?.legal_stories != null) facts.push(["Legal stories", String(building.legal_stories)]);
  if (building?.legal_class_a_units != null) facts.push(["Class A units", String(building.legal_class_a_units)]);
  if (building?.dob_building_class) facts.push(["DOB class", building.dob_building_class.replace(/\s+/g, " ")]);
  if (r.footprint_bins_on_lot.length > 0) facts.push(["Buildings on lot", String(r.footprint_bins_on_lot.length)]);

  return (
    <section className="relative overflow-hidden bg-navy text-white print:bg-white print:text-ink" data-testid="identity-card">
      <div className="pointer-events-none absolute inset-0 opacity-[0.07] [background-image:linear-gradient(#ffffff_1px,transparent_1px),linear-gradient(90deg,#ffffff_1px,transparent_1px)] [background-size:56px_56px]" aria-hidden />
      <div className="container-site relative py-8 sm:py-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow text-accent">{lotOnly ? "Tax lot identified" : "Building identified"}</span>
              <span
                className={`chip ${live ? "bg-white/10 text-white ring-white/20" : "bg-white/10 text-sky-100 ring-white/20"}`}
                data-testid="data-mode-badge"
              >
                <Radio className="h-3 w-3" aria-hidden />
                {live ? "Live NYC data" : "Demo data · recorded Oct 2026"}
              </span>
            </div>
            <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.08] tracking-tight text-white sm:text-5xl print:text-ink">
              {name ? name.street : r.label}
            </h1>
            {name && name.rest && <p className="mt-1.5 text-lg text-slate-300 print:text-ink-soft">{name.rest}</p>}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <span className={`chip py-1 ${lotOnly ? "bg-amber-100 text-amber-900 ring-amber-300" : "bg-white text-navy ring-white"}`}>
                {lotOnly ? <Landmark className="h-3.5 w-3.5" aria-hidden /> : <Building2 className="h-3.5 w-3.5" aria-hidden />}
                {lotOnly ? "Tax-lot level: building not verified" : "Building-level match"}
              </span>
              <span className={`chip py-1 ${CONF_CLS[r.confidence]}`} data-testid="confidence">
                <BadgeCheck className="h-3.5 w-3.5" aria-hidden />
                {r.confidence_label}
              </span>
              {asOf && (
                <span className="chip bg-white/10 py-1 text-slate-200 ring-white/20">
                  <CalendarClock className="h-3.5 w-3.5" aria-hidden />
                  Data as of {fmtDate(asOf.toISOString().slice(0, 10))}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 flex-col gap-2 lg:items-end">
            <ExportBar report={report} />
            <span className="no-print inline-flex items-center gap-1 text-xs text-slate-400">
              <Download className="h-3 w-3" aria-hidden /> Report {report.report_id.slice(0, 8)} · generated{" "}
              {fmtDate(report.generated_at)}
            </span>
          </div>
        </div>

        <dl className="mt-8 grid grid-cols-2 overflow-hidden rounded-2xl border border-white/15 sm:grid-cols-3 lg:grid-cols-6">
          {facts.map(([k, v]) => (
            <div key={k} className="-mb-px -mr-px border-b border-r border-white/15 bg-white/[0.04] px-4 py-3.5">
              <dt className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{TERMS[k] ? <Term k={TERMS[k]}>{k}</Term> : k}</dt>
              <dd className="mt-1 truncate font-mono text-[15px] text-white print:text-ink" title={v}>
                {v}
              </dd>
            </div>
          ))}
        </dl>

        <div className="mt-5 flex flex-col gap-3 text-sm md:flex-row md:items-start md:justify-between">
          <p className="max-w-3xl leading-relaxed text-slate-300 print:text-ink-soft">
            {r.explanation}
            {building?.registration_status && (
              <span className="mt-1 block text-slate-400">
                <span className="font-medium text-slate-200">
                  HPD: {REG_LABEL[building.registration_status] ?? "registration record"}
                </span>
                {building.registration_end_date && <> · end date {fmtDate(building.registration_end_date)}</>}
                {building.registration_note && <>. {building.registration_note}</>}
              </span>
            )}
          </p>
          {r.links.length > 0 && (
            <div className="no-print flex shrink-0 flex-wrap gap-x-4 gap-y-1.5 md:max-w-sm md:justify-end">
              {r.links.map((l) => (
                <ExternalLink key={l.url} href={l.url} className="text-sm !text-accent hover:!text-white">
                  {l.label.replace(" (NYC Planning)", "")}
                </ExternalLink>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
