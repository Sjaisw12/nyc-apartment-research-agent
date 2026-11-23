"use client";

import { useEffect, useMemo, useState } from "react";
import type { EvidenceItem, Finding, ResearchReport } from "@/lib/types";
import { CATEGORY, fmtDate } from "@/lib/format";
import { markdownUrl } from "@/lib/api";
import { IdentityCard } from "./IdentityCard";
import { FindingCard } from "./FindingCard";
import { Timeline } from "./Timeline";
import { SourcesTable } from "./Sources";
import { CategoryChip, ScopeChip, SectionHeading } from "./ui";

const TONE: Record<string, string> = {
  attention: "text-amber-700",
  good: "text-emerald-700",
  neutral: "text-ink",
};

function jump(id: string) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
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
    <div className="no-print flex flex-wrap gap-2" data-testid="export-bar">
      <button type="button" className="btn-ghost py-2 text-xs" onClick={() => window.print()}>
        Print / Save PDF
      </button>
      <a className="btn-ghost py-2 text-xs" href={markdownUrl(report.report_id)} download>
        Markdown
      </a>
      <button type="button" className="btn-ghost py-2 text-xs" onClick={downloadJson}>
        JSON
      </button>
    </div>
  );
}

function SummaryItem({ f }: { f: Finding }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => jump(`finding-${f.id}`)}
        className="group w-full rounded-xl border border-stone-200 bg-white p-3 text-left transition hover:border-brand/40 hover:shadow-card"
      >
        <span className="flex flex-wrap gap-1.5">
          <CategoryChip category={f.category} />
          <ScopeChip scope={f.scope} />
        </span>
        <span className="mt-1.5 block text-sm font-semibold text-ink group-hover:text-brand">{f.title}</span>
        <span className="mt-0.5 block text-sm leading-relaxed text-ink-soft">{f.statement}</span>
      </button>
    </li>
  );
}

function AgentBox({ report }: { report: ResearchReport }) {
  const a = report.agent;
  if (!a) return null;
  if (a.status !== "completed" || a.paragraphs.length === 0) {
    return (
      <p className="mt-4 rounded-xl border border-dashed border-stone-300 px-4 py-3 text-xs leading-relaxed text-ink-faint" data-testid="agent-status">
        <span className="font-semibold text-ink-soft">AI explanation: </span>
        {a.status_detail} Every statement on this page comes from fixed rules over the cited records.
      </p>
    );
  }
  return (
    <div className="mt-4 rounded-xl bg-brand-light/60 p-4" data-testid="agent-analysis">
      <p className="text-xs font-semibold uppercase tracking-wide text-brand">
        AI explanation · checked against the records
      </p>
      <div className="mt-2 space-y-2 text-sm leading-relaxed text-ink">
        {a.paragraphs.map((p, i) => (
          <p key={i}>
            {p.text}{" "}
            {p.finding_ids.map((id) => (
              <button key={id} type="button" onClick={() => jump(`finding-${id}`)} className="font-mono text-[11px] text-brand hover:underline">
                [{id}]
              </button>
            ))}
          </p>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-faint">
        Written by {a.provider} {a.model} from the findings below. Each paragraph passed a citation and number check;
        {a.rejected.length > 0 ? ` ${a.rejected.length} paragraph(s) failed and were removed.` : " none were removed."}
      </p>
    </div>
  );
}

export function Report({ report }: { report: ResearchReport }) {
  const evidence = useMemo(() => new Map<string, EvidenceItem>(report.evidence.map((e) => [e.id, e])), [report]);
  const findings = useMemo(() => new Map(report.findings.map((f) => [f.id, f])), [report]);
  const summary = report.sections.find((s) => s.id === "summary");
  const body = report.sections.filter((s) => s.id !== "summary" && s.finding_ids.length > 0);
  const nav = [
    { id: "summary", title: "Summary" },
    { id: "timeline", title: "Timeline" },
    ...body.map((s) => ({ id: s.id, title: s.title })),
    { id: "questions", title: "Questions to ask" },
    { id: "sources", title: "Sources & freshness" },
    { id: "method", title: "Limitations & method" },
  ];
  const [active, setActive] = useState("summary");
  useEffect(() => {
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setActive(vis[0].target.id);
      },
      { rootMargin: "-80px 0px -70% 0px" },
    );
    nav.forEach((n) => {
      const el = document.getElementById(n.id);
      if (el) obs.observe(el);
    });
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.report_id]);

  const keyFindings = (summary?.finding_ids ?? []).map((id) => findings.get(id)).filter(Boolean) as Finding[];

  return (
    <div className="space-y-6" data-testid="report">
      {report.demo_notice && (
        <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-2.5 text-sm text-sky-900" data-testid="demo-notice">
          {report.demo_notice}
        </div>
      )}
      {!report.resolution.in_demo_dataset && report.run.mode === "demo" && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" data-testid="not-in-demo">
          This address was identified, but its records are not in the offline demo set, so no agency data was checked.
          This is not evidence that there are no records. Try one of the example addresses, or run the app in live mode.
        </div>
      )}

      <IdentityCard r={report.resolution} building={report.building} />

      {report.stats.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5" data-testid="stats">
          {report.stats.map((s) => (
            <button
              key={s.label}
              type="button"
              onClick={() => s.finding_id && jump(`finding-${s.finding_id}`)}
              className="card p-4 text-left transition hover:border-brand/40"
            >
              <span className={`block font-display text-3xl font-semibold tabular-nums ${TONE[s.tone]}`}>{s.value}</span>
              <span className="mt-1 block text-xs font-medium leading-snug text-ink">{s.label}</span>
              {s.hint && <span className="mt-0.5 block text-[11px] leading-snug text-ink-faint">{s.hint}</span>}
            </button>
          ))}
        </div>
      )}

      <div className="lg:grid lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-8">
        <aside className="no-print hidden lg:block">
          <nav className="sticky top-6 space-y-0.5 text-sm" aria-label="Report sections">
            {nav.map((n) => (
              <a
                key={n.id}
                href={`#${n.id}`}
                className={`block rounded-lg px-3 py-1.5 ${active === n.id ? "bg-brand-light font-medium text-brand" : "text-ink-soft hover:bg-stone-100"}`}
              >
                {n.title}
              </a>
            ))}
          </nav>
        </aside>

        <div className="min-w-0 space-y-10">
          <section className="space-y-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <SectionHeading id="summary" title="Executive summary" intro={report.headline} />
              <ExportBar report={report} />
            </div>
            {keyFindings.length > 0 ? (
              <ul className="grid gap-3 md:grid-cols-2" data-testid="key-findings">
                {keyFindings.map((f) => (
                  <SummaryItem key={f.id} f={f} />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-soft">No significant findings were selected for this address.</p>
            )}
            <AgentBox report={report} />
            <details className="no-print text-xs text-ink-soft">
              <summary className="cursor-pointer font-medium text-ink">How to read the labels</summary>
              <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
                {Object.entries(CATEGORY).map(([k, v]) => (
                  <li key={k} className="flex items-start gap-2">
                    <span className={`chip shrink-0 ${v.cls}`}>{v.label}</span>
                    <span>{v.help}</span>
                  </li>
                ))}
              </ul>
            </details>
          </section>

          <section className="space-y-3">
            <SectionHeading
              id="timeline"
              title="Timeline"
              intro="Dated records by year and agency. 311 requests are filed against the whole tax lot, so they only appear under the tax-lot view."
            />
            <Timeline events={report.timeline} />
          </section>

          {body.map((s) => (
            <section key={s.id} className="space-y-3">
              <SectionHeading id={s.id} title={s.title} intro={s.intro} />
              <div className="space-y-3">
                {s.finding_ids.map((id) => {
                  const f = findings.get(id);
                  return f ? <FindingCard key={id} f={f} evidence={evidence} /> : null;
                })}
              </div>
            </section>
          ))}

          <section className="space-y-3">
            <SectionHeading id="questions" title="Questions to ask" intro="Questions a renter could put to the landlord or broker, based on the records above." />
            <ol className="card list-decimal space-y-2 py-4 pl-10 pr-5 text-sm leading-relaxed text-ink" data-testid="questions">
              {report.questions_to_ask.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ol>
          </section>

          <section className="space-y-3">
            <SectionHeading id="sources" title="Sources & freshness" intro="Every dataset checked, whether it was fully retrieved, and the date of its last update." />
            <SourcesTable sources={report.sources} />
          </section>

          <section className="space-y-3">
            <SectionHeading id="method" title="Limitations & method" />
            <div className="card space-y-4 p-5 text-sm leading-relaxed text-ink-soft">
              <ul className="list-disc space-y-1.5 pl-5">
                {report.limitations.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
              <div className="grid gap-4 border-t border-stone-100 pt-4 sm:grid-cols-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Verification</p>
                  <p className="mt-1" data-testid="verification">
                    {report.verification.passed ? "Passed" : "Issues found"}: {report.verification.findings_checked} findings,{" "}
                    {report.verification.facts_checked} counts re-computed from the records, {report.verification.citations_checked.toLocaleString()} citations checked.
                  </p>
                  {report.verification.issues.map((i, k) => (
                    <p key={k} className="text-xs text-rose-700">
                      {i.finding_id}: {i.problem}
                    </p>
                  ))}
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">Run</p>
                  <p className="mt-1">
                    {report.run.mode === "demo" ? "Demo data" : "Live NYC Open Data"} · {report.run.elapsed_seconds.toFixed(1)} s ·{" "}
                    {report.run.external_requests} API requests · {report.run.llm_calls} LLM calls
                  </p>
                  <p className="mt-1 text-xs text-ink-faint">
                    {report.run.steps.map((s) => `${s.name.replace(/_/g, " ")} (${s.detail ?? s.status})`).join(" → ")}
                  </p>
                  <p className="mt-1 text-xs text-ink-faint">
                    Report {report.report_id.slice(0, 8)} · generated {fmtDate(report.generated_at)} · schema {report.schema_version}
                  </p>
                </div>
              </div>
              <p className="border-t border-stone-100 pt-4 text-xs">{report.disclaimer}</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
