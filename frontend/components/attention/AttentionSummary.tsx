"use client";

import { useState } from "react";
import {
  AlertOctagon,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  CircleHelp,
  ClipboardList,
  ExternalLink as ExternalIcon,
  Info,
  ListChecks,
  MessageCircleQuestion,
  SearchCheck,
} from "lucide-react";
import type { AttentionLabel, AttentionPoint, AttentionSummary as Summary, CoverageSource } from "@/lib/types";
import { fmtSourceDate } from "@/lib/format";

/** Visual treatment per label. Semantic colour only: amber for caution, slate for unknown. */
export const LABEL_STYLE: Record<AttentionLabel, { icon: typeof Info; badge: string; bar: string; tone: string }> = {
  priority_follow_up: {
    icon: AlertOctagon,
    badge: "bg-amber-100 text-amber-900 ring-amber-300",
    bar: "bg-amber-500",
    tone: "text-amber-700",
  },
  some_items_to_check: {
    icon: ListChecks,
    badge: "bg-brand-light text-brand-dark ring-brand/30",
    bar: "bg-brand",
    tone: "text-brand",
  },
  no_priority_items: {
    icon: SearchCheck,
    badge: "bg-slate-100 text-ink ring-line-strong",
    bar: "bg-navy-700",
    tone: "text-navy-700",
  },
  insufficient_information: {
    icon: CircleHelp,
    badge: "bg-slate-100 text-slate-700 ring-slate-300",
    bar: "bg-slate-400",
    tone: "text-slate-600",
  },
};

const LEVEL: Record<AttentionPoint["level"], { label: string; cls: string; dot: string }> = {
  priority: { label: "Priority", cls: "bg-amber-50 text-amber-900 ring-amber-300", dot: "bg-amber-500" },
  check: { label: "Worth checking", cls: "bg-brand-50 text-brand-dark ring-brand/25", dot: "bg-brand" },
  info: { label: "Good to know", cls: "bg-slate-50 text-slate-700 ring-slate-200", dot: "bg-slate-400" },
};

const COVER: Record<CoverageSource["state"], { icon: typeof Info; cls: string; label: string }> = {
  complete: { icon: CheckCircle2, cls: "text-brand", label: "Checked" },
  partial: { icon: CircleDashed, cls: "text-amber-600", label: "Partial" },
  unavailable: { icon: AlertOctagon, cls: "text-rose-600", label: "Not checked" },
  not_applicable: { icon: Info, cls: "text-slate-500", label: "Not applicable" },
  not_checked: { icon: AlertOctagon, cls: "text-rose-600", label: "Not checked" },
};

function jump(id?: string | null) {
  if (!id) return;
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

function Point({ p, live }: { p: AttentionPoint; live: Set<string> }) {
  const lv = LEVEL[p.level];
  const target = p.section && live.has(p.section) ? p.section : null;
  return (
    <li className="relative rounded-2xl border border-line bg-white p-5 shadow-card" data-testid="attention-point" data-level={p.level}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`chip ${lv.cls}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${lv.dot}`} aria-hidden />
          {lv.label}
        </span>
        <h3 className="text-[15px] font-semibold text-ink">{p.heading}</h3>
      </div>
      <p className="mt-2.5 max-w-prose text-[15px] leading-relaxed text-ink-soft">{p.text}</p>
      {p.ask && (
        <p className="mt-3 flex gap-2 rounded-xl bg-paper px-3.5 py-2.5 text-sm leading-relaxed text-ink">
          <MessageCircleQuestion className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden />
          {p.ask}
        </p>
      )}
      {target && (
        <button
          type="button"
          onClick={() => jump(target)}
          className="no-print mt-3 inline-flex items-center gap-1 text-sm font-semibold text-brand hover:text-brand-dark"
        >
          {p.evidence_ids.length > 0 ? `See the ${p.evidence_ids.length.toLocaleString()} cited records` : "See details"}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
    </li>
  );
}

export function CoverageCard({ a }: { a: Summary }) {
  const c = a.coverage;
  const tone = c.level === "complete" ? "text-brand" : c.level === "partial" ? "text-amber-700" : "text-rose-700";
  return (
    <div className="card p-5" data-testid="data-coverage" data-level={c.level}>
      <p className="eyebrow">Data coverage</p>
      <p className={`mt-2 font-semibold ${tone}`}>{c.label}</p>
      <p className="mt-1 text-sm leading-relaxed text-ink-soft">{c.detail}</p>
      <ul className="mt-4 space-y-2.5">
        {c.sources.map((s) => {
          const st = COVER[s.state];
          return (
            <li key={s.source} className="flex items-start gap-2.5 text-sm" title={s.note}>
              <st.icon className={`mt-0.5 h-4 w-4 shrink-0 ${st.cls}`} aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="font-medium text-ink">{s.name}</span>
                <span className="block text-xs text-ink-faint">
                  {st.label}
                  {s.state === "partial" || s.state === "unavailable" ? ` · ${s.note}` : ""}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
      {(c.oldest_data_as_of || c.newest_data_as_of) && (
        <p className="mt-4 border-t border-line pt-3 text-xs text-ink-faint">
          Source data dated {fmtSourceDate(c.oldest_data_as_of)}
          {c.newest_data_as_of && c.newest_data_as_of !== c.oldest_data_as_of ? ` to ${fmtSourceDate(c.newest_data_as_of)}` : ""}.
        </p>
      )}
    </div>
  );
}

export function AttentionSummary({ a, sections }: { a: Summary; sections: string[] }) {
  const [why, setWhy] = useState(false);
  const st = LABEL_STYLE[a.label];
  const live = new Set([...sections, "sources", "timeline"]);
  return (
    <section id="overview" className="scroll-mt-28" aria-labelledby="attention-title" data-testid="attention-summary" data-label={a.label}>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="card overflow-hidden">
            <div className={`h-1.5 ${st.bar}`} aria-hidden />
            <div className="p-6 sm:p-8">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="eyebrow" id="attention-title">
                  Building Attention Summary
                </p>
                <span className="text-xs text-ink-faint">Automated evidence summary · not an AI judgment</span>
              </div>
              <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
                <span className={`inline-flex items-center gap-2 self-start rounded-xl px-3.5 py-2 text-sm font-semibold ring-1 ring-inset ${st.badge}`} data-testid="attention-label">
                  <st.icon className="h-[18px] w-[18px]" aria-hidden />
                  {a.label_text}
                </span>
              </div>
              <h2 className="mt-4 max-w-3xl font-display text-[1.75rem] font-semibold leading-tight tracking-tight text-ink sm:text-[2.1rem]">
                {a.headline}
              </h2>
              <p className="mt-2 max-w-3xl text-[15px] leading-relaxed text-ink-soft">{a.label_meaning}</p>

              <button
                type="button"
                onClick={() => setWhy((w) => !w)}
                aria-expanded={why}
                aria-controls="why-panel"
                className="no-print mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:text-brand-dark"
              >
                Why am I seeing this?
                <ChevronDown className={`h-4 w-4 transition ${why ? "rotate-180" : ""}`} aria-hidden />
              </button>
              <div id="why-panel" hidden={!why} className="print-expand mt-3 rounded-2xl bg-paper p-5 text-sm leading-relaxed text-ink-soft">
                {a.reasons.length > 0 && (
                  <>
                    <p className="font-semibold text-ink">What triggered this label</p>
                    <ul className="mt-1.5 list-disc space-y-1 pl-5">
                      {a.reasons.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </>
                )}
                <p className="mt-4 font-semibold text-ink">The rules Stoop uses</p>
                <ol className="mt-1.5 list-decimal space-y-1 pl-5">
                  {a.rules.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ol>
                <p className="mt-4 text-xs text-ink-faint">
                  {a.disclaimer}{" "}
                  <a href="/methodology" className="link">
                    Read the full methodology
                  </a>
                  .
                </p>
              </div>
            </div>
          </div>

          <ul className="mt-4 grid gap-3" data-testid="attention-points">
            {a.points.map((p) => (
              <Point key={p.id} p={p} live={live} />
            ))}
          </ul>
        </div>

        <aside className="space-y-4">
          <CoverageCard a={a} />
          {a.next_steps.length > 0 && (
            <div className="card p-5">
              <p className="eyebrow flex items-center gap-1.5">
                <ClipboardList className="h-3.5 w-3.5" aria-hidden /> Ask before you sign
              </p>
              <ol className="mt-3 space-y-2.5 text-sm leading-relaxed text-ink">
                {a.next_steps.slice(0, 3).map((q, i) => (
                  <li key={q} className="flex gap-2.5">
                    <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-light text-[11px] font-semibold text-brand-dark">
                      {i + 1}
                    </span>
                    {q}
                  </li>
                ))}
              </ol>
              <a href="#questions" className="no-print mt-3 inline-block text-sm font-semibold text-brand hover:text-brand-dark">
                All questions →
              </a>
            </div>
          )}
          {a.verify_links.length > 0 && (
            <div className="card p-5">
              <p className="eyebrow">Verify it yourself</p>
              <ul className="mt-3 space-y-2 text-sm">
                {a.verify_links.map((l) => (
                  <li key={l.url}>
                    <a href={l.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-medium text-brand hover:text-brand-dark">
                      {l.label}
                      <ExternalIcon className="h-3.5 w-3.5" aria-hidden />
                      <span className="sr-only">(opens in a new tab)</span>
                    </a>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs leading-relaxed text-ink-faint">The agencies&apos; own sites show the current status of each record.</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
