import Link from "next/link";
import { AlertOctagon, ArrowUpRight, CheckCircle2, MessageCircleQuestion } from "lucide-react";
import { investigateHref } from "@/lib/examples";

// A static preview of the Building Attention Summary for the recorded demo report of
// 1134 West Farms Rd (October 2026). Every number here appears in that report.
const ADDRESS = "1134 West Farms Rd, Bronx, NY 10459";
const POINTS = [
  {
    level: "Priority",
    cls: "bg-amber-50 text-amber-900 ring-amber-300",
    title: "Housing violations",
    text: "HPD lists 111 open violations, including 40 in Class C, its most serious category.",
  },
  {
    level: "Worth checking",
    cls: "bg-brand-50 text-brand-dark ring-brand/25",
    title: "Resident-reported issues",
    text: "29 complaints to HPD in the last 12 months, mostly heat and hot water. Reports, not proof.",
  },
  {
    level: "Worth checking",
    cls: "bg-brand-50 text-brand-dark ring-brand/25",
    title: "Recorded orders (historical)",
    text: "1 order event in DOB's data. It does not show whether any order applies today.",
  },
];

export function ReportPreview() {
  return (
    <div className="relative">
      <div className="card overflow-hidden shadow-hero ring-1 ring-white/10">
        <div className="flex items-center justify-between gap-3 border-b border-line bg-paper px-5 py-3">
          <span className="truncate text-xs font-semibold text-ink">1134 West Farms Road · Bronx</span>
          <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-ink-faint">
            <CheckCircle2 className="h-3 w-3 text-brand" aria-hidden /> BIN 2006185 verified
          </span>
        </div>
        <div className="p-5 sm:p-6">
          <div className="h-1 w-full rounded-full bg-amber-500" aria-hidden />
          <p className="eyebrow mt-4 text-[10px]">Building Attention Summary</p>
          <span className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-900 ring-1 ring-inset ring-amber-300">
            <AlertOctagon className="h-3.5 w-3.5" aria-hidden /> Priority follow-up
          </span>
          <p className="mt-3 font-display text-xl font-semibold leading-snug text-ink sm:text-2xl">
            Several records deserve follow-up before signing a lease.
          </p>
          <ul className="mt-4 space-y-2.5">
            {POINTS.map((p) => (
              <li key={p.title} className="rounded-xl border border-line p-3.5">
                <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                  <span className={`chip py-0 text-[10px] ${p.cls}`}>{p.level}</span>
                  {p.title}
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">{p.text}</p>
              </li>
            ))}
          </ul>
          <p className="mt-3 flex gap-2 rounded-xl bg-paper px-3 py-2.5 text-[13px] text-ink">
            <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden />
            Ask which open violations have been fixed and certified to HPD.
          </p>
        </div>
        <Link
          href={investigateHref(ADDRESS)}
          className="group flex items-center justify-between border-t border-line bg-white px-5 py-3.5 text-sm font-semibold text-brand hover:bg-brand-50"
        >
          Open the full sample report
          <ArrowUpRight className="h-4 w-4 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
        </Link>
      </div>
      <p className="mt-3 text-center text-xs text-slate-400">Sample from recorded demo data (October 2026)</p>
    </div>
  );
}
