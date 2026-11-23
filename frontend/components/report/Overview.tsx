"use client";

import { AlertTriangle, Ban, CheckCircle2, ClipboardList, Gavel, HardHat, Home, Megaphone, type LucideIcon } from "lucide-react";
import type { ResearchReport, SourceStatus, Stat } from "@/lib/types";
import { fmtSourceDate } from "@/lib/format";

function statMeta(label: string): { icon: LucideIcon; source?: string } {
  const l = label.toLowerCase();
  if (l.includes("hpd violation")) return { icon: Home, source: "hpd_violations" };
  if (l.includes("hpd complaint")) return { icon: ClipboardList, source: "hpd_complaints" };
  if (l.includes("dob complaint")) return { icon: HardHat, source: "dob_complaints" };
  if (l.includes("ecb")) return { icon: Gavel, source: "ecb_violations" };
  if (l.includes("311")) return { icon: Megaphone, source: "sr311" };
  return { icon: ClipboardList };
}

const TONE: Record<Stat["tone"], string> = {
  attention: "text-caution",
  good: "text-brand",
  neutral: "text-ink",
};

function coverage(s?: SourceStatus) {
  if (!s) return null;
  if (s.status === "records_found" || s.status === "no_records_found")
    return s.complete ? { label: "Complete", cls: "text-brand", icon: CheckCircle2 } : { label: "Partial", cls: "text-amber-700", icon: AlertTriangle };
  if (s.status === "incomplete_pagination") return { label: "Partial", cls: "text-amber-700", icon: AlertTriangle };
  return { label: "Not checked", cls: "text-rose-700", icon: Ban };
}

function jump(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function Overview({ report }: { report: ResearchReport }) {
  const bySource = new Map(report.sources.map((s) => [s.source, s]));
  const stats = report.stats;
  const cols =
    stats.length >= 5 ? "lg:grid-cols-5" : stats.length === 4 ? "lg:grid-cols-4" : stats.length === 3 ? "lg:grid-cols-3" : "lg:grid-cols-2";
  return (
    <section aria-label="Overview" className="space-y-4">
      {stats.length > 0 && (
        <div
          className={`grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-3 ${cols} max-md:[&>*:last-child:nth-child(odd)]:col-span-2`}
          data-testid="stats"
        >
          {stats.map((s) => {
            const m = statMeta(s.label);
            const cov = coverage(m.source ? bySource.get(m.source) : undefined);
            const Icon = m.icon;
            return (
              <button
                key={s.label}
                type="button"
                onClick={() => s.finding_id && jump(`finding-${s.finding_id}`)}
                className="card group flex flex-col p-4 text-left transition hover:-translate-y-0.5 hover:border-brand/30 hover:shadow-lift sm:p-5"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="grid h-9 w-9 place-items-center rounded-xl bg-paper text-ink-soft ring-1 ring-line group-hover:text-brand">
                    <Icon className="h-[18px] w-[18px]" aria-hidden />
                  </span>
                  {cov && (
                    <span className={`inline-flex items-center gap-1 text-[11px] font-semibold ${cov.cls}`}>
                      <cov.icon className="h-3 w-3" aria-hidden />
                      {cov.label}
                    </span>
                  )}
                </span>
                <span className={`mt-4 block font-display text-[2.4rem] font-semibold leading-none tabular-nums ${TONE[s.tone]}`}>
                  {s.value}
                </span>
                <span className="mt-2 block text-sm font-semibold leading-snug text-ink">{s.label}</span>
                {s.hint && <span className="mt-1 block text-xs leading-snug text-ink-faint">{s.hint}</span>}
              </button>
            );
          })}
        </div>
      )}
      <div className="card flex flex-col gap-3 p-4 sm:p-5 lg:flex-row lg:items-center" data-testid="coverage">
        <p className="shrink-0 text-sm font-semibold text-ink lg:w-44">
          Source coverage
          <span className="block text-xs font-normal text-ink-faint">What was checked, and how fully</span>
        </p>
        <ul className="grid flex-1 grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {report.sources.map((s) => {
            const cov = coverage(s)!;
            return (
              <li key={s.source}>
                <button
                  type="button"
                  onClick={() => jump("sources")}
                  className="flex w-full items-center gap-2.5 rounded-xl border border-line bg-paper/40 px-3 py-2 text-left hover:border-brand/30"
                  title={s.status_detail}
                >
                  <cov.icon className={`h-4 w-4 shrink-0 ${cov.cls}`} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-medium text-ink">
                      {s.agency} · {s.dataset_name}
                    </span>
                    <span className="block text-[11px] text-ink-faint">
                      <span className={cov.cls}>{cov.label}</span> ·{" "}
                      {cov.label === "Not checked"
                        ? "not counted"
                        : `${s.records.toLocaleString()}${s.status === "incomplete_pagination" ? "+" : ""} records`}{" "}
                      · {s.data_as_of ? fmtSourceDate(s.data_as_of) : "data date unknown"}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
