"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Bot, Info, ListChecks, ShieldCheck } from "lucide-react";
import type { Category, EvidenceItem, Finding, ResearchReport } from "@/lib/types";
import { CATEGORY, fmtDate } from "@/lib/format";
import { IdentityCard } from "./IdentityCard";
import { AttentionSummary } from "@/components/attention/AttentionSummary";
import { Overview } from "./Overview";
import { FindingCard } from "./FindingCard";
import { Timeline } from "./Timeline";
import { SourcesTable } from "./Sources";
import { CATEGORY_DOT, CategoryChip, ScopeChip, SectionHeading } from "./ui";

function jump(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

const FILTERS: { key: string; label: string; cats: Category[] | null }[] = [
  { key: "all", label: "All", cats: null },
  { key: "verified", label: "Verified", cats: ["verified_record"] },
  { key: "allegation", label: "Allegations", cats: ["complaint_allegation"] },
  { key: "enforcement", label: "Enforcement & history", cats: ["enforcement_action", "historical_event"] },
  { key: "uncertain", label: "Uncertain & unavailable", cats: ["uncertain", "unavailable"] },
];

function SummaryItem({ f }: { f: Finding }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => jump(`finding-${f.id}`)}
        className="group flex h-full w-full flex-col rounded-2xl border border-line bg-white p-5 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand/30 hover:shadow-lift"
      >
        <span className="flex flex-wrap gap-1.5">
          <CategoryChip category={f.category} withIcon />
          <ScopeChip scope={f.scope} />
        </span>
        <span className="mt-3 block text-base font-semibold leading-snug text-ink group-hover:text-brand">{f.title}</span>
        <span className="mt-1.5 block text-sm leading-relaxed text-ink-soft">{f.statement}</span>
        <span className="mt-auto pt-3 text-xs font-semibold text-brand opacity-0 transition group-hover:opacity-100">
          See details and records →
        </span>
      </button>
    </li>
  );
}

function AgentBox({ report }: { report: ResearchReport }) {
  const a = report.agent;
  if (!a) return null;
  if (a.status !== "completed" || a.paragraphs.length === 0) {
    return (
      <p className="flex gap-3 rounded-2xl border border-dashed border-line bg-white/60 px-4 py-3 text-sm leading-relaxed text-ink-faint" data-testid="agent-status">
        <Bot className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>
          <span className="font-semibold text-ink-soft">AI explanation is off. </span>
          The summaries in this report are automated evidence summaries: fixed rules applied to the cited records, with every
          number re-checked. No AI model wrote them.
        </span>
      </p>
    );
  }
  return (
    <div className="rounded-2xl bg-brand-light/60 p-5" data-testid="agent-analysis">
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-brand">
        <Bot className="h-4 w-4" aria-hidden /> AI explanation · checked against the records
      </p>
      <div className="mt-3 space-y-2 text-[15px] leading-relaxed text-ink">
        {a.paragraphs.map((p, i) => (
          <p key={i}>
            {p.text}{" "}
            {p.finding_ids.map((id) => (
              <button key={id} type="button" onClick={() => jump(`finding-${id}`)} className="font-mono text-xs text-brand hover:underline">
                [{id}]
              </button>
            ))}
          </p>
        ))}
      </div>
      <p className="mt-3 text-xs text-ink-faint">
        Written by {a.provider} {a.model} from the findings below. Each paragraph passed a citation and number check
        {a.rejected.length > 0 ? `; ${a.rejected.length} paragraph(s) failed and were removed.` : "."}
      </p>
    </div>
  );
}

export function Report({ report }: { report: ResearchReport }) {
  const evidence = useMemo(() => new Map<string, EvidenceItem>(report.evidence.map((e) => [e.id, e])), [report]);
  const findings = useMemo(() => new Map(report.findings.map((f) => [f.id, f])), [report]);
  const [filter, setFilter] = useState("all");
  const cats = FILTERS.find((x) => x.key === filter)?.cats ?? null;
  const visible = (f?: Finding) => !!f && (!cats || cats.includes(f.category));

  const summary = report.sections.find((s) => s.id === "summary");
  const body = report.sections.filter((s) => s.id !== "summary" && s.finding_ids.length > 0);
  const bodyVisible = body.filter((s) => s.finding_ids.some((id) => visible(findings.get(id))));
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    const seen = new Set<string>();
    for (const s of body)
      for (const id of s.finding_ids) {
        if (seen.has(id)) continue;
        seen.add(id);
        const f = findings.get(id);
        if (!f) continue;
        for (const fl of FILTERS) if (!fl.cats || fl.cats.includes(f.category)) c[fl.key] = (c[fl.key] ?? 0) + 1;
      }
    return c;
  }, [body, findings]);

  const has = (id: string) => body.some((s) => s.id === id);
  const firstOf = (...ids: string[]) => ids.find(has);
  const complaintsId = firstOf("hpd_complaints", "sr311");
  const dobId = firstOf("dob_complaints", "ecb_violations", "orders");
  const nav: { id: string; title: string; n?: number }[] = [
    ...(report.attention ? [{ id: "overview", title: "Overview" }] : []),
    { id: "summary", title: "Key findings" },
    { id: "questions", title: "Questions to ask" },
    ...(has("hpd_violations") ? [{ id: "hpd_violations", title: "Housing violations" }] : []),
    ...(complaintsId ? [{ id: complaintsId, title: "Complaints" }] : []),
    ...(dobId ? [{ id: dobId, title: "DOB / enforcement" }] : []),
    { id: "timeline", title: "Historical timeline" },
    { id: "sources", title: "Sources and evidence" },
    { id: "findings", title: "Full report" },
    { id: "method", title: "Limitations & method" },
  ];
  const [active, setActive] = useState(report.attention ? "overview" : "summary");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const obs = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis[0]) setActive(vis[0].target.id);
      },
      { rootMargin: "-120px 0px -65% 0px" },
    );
    nav.forEach((n) => {
      const el = document.getElementById(n.id);
      if (el) obs.observe(el);
    });
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report.report_id, filter]);

  const keyFindings = ((summary?.finding_ids ?? []).map((id) => findings.get(id)).filter(Boolean) as Finding[]).slice(0, 4);
  const notInDemo = !report.resolution.in_demo_dataset && report.run.mode === "demo";

  return (
    <div data-testid="report">
      <IdentityCard report={report} />

      <div className="container-site space-y-6 py-8">
        {report.demo_notice && (
          <p className="flex items-start gap-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900" data-testid="demo-notice">
            <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            {report.demo_notice}
          </p>
        )}
        {notInDemo && (
          <p className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" data-testid="not-in-demo">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            This address was identified, but its records are not in the offline demo set, so no agency data was checked.
            This is not evidence that there are no records. Run Stoop in live mode to check it.
          </p>
        )}
        {report.attention && <AttentionSummary a={report.attention} sections={body.map((s) => s.id)} />}
        <div className="pt-2">
          <p className="eyebrow mb-3">At a glance</p>
          <Overview report={report} />
        </div>
      </div>

      {/* Mobile section nav */}
      <nav
        className="no-print sticky top-16 z-30 -mt-2 border-y border-line bg-paper/95 backdrop-blur lg:hidden"
        aria-label="Report sections"
      >
        <div className="container-site flex gap-1.5 overflow-x-auto py-2.5 [scrollbar-width:none]">
          {nav.map((n) => (
            <a
              key={n.id}
              href={`#${n.id}`}
              className={`shrink-0 rounded-full px-3 py-1.5 text-sm font-medium ${
                active === n.id ? "bg-ink text-white" : "bg-white text-ink-soft ring-1 ring-line"
              }`}
            >
              {n.title}
            </a>
          ))}
        </div>
      </nav>

      <div className="container-site print-full grid gap-10 pb-8 pt-6 lg:grid-cols-[250px_minmax(0,1fr)] lg:gap-12">
        <aside className="no-print hidden lg:block">
          <div className="sticky top-24 space-y-6">
            <nav className="space-y-0.5 text-sm" aria-label="Report sections">
              <p className="eyebrow mb-2 px-3">On this page</p>
              {nav.map((n) => (
                <a
                  key={n.id}
                  href={`#${n.id}`}
                  aria-current={active === n.id ? "true" : undefined}
                  className={`flex items-center justify-between gap-2 rounded-lg px-3 py-2 ${
                    active === n.id ? "bg-white font-semibold text-ink shadow-sm ring-1 ring-line" : "text-ink-soft hover:bg-white/70 hover:text-ink"
                  }`}
                >
                  <span>{n.title}</span>
                  {"n" in n && n.n ? <span className="text-xs tabular-nums text-ink-faint">{n.n}</span> : null}
                </a>
              ))}
            </nav>
            <div className="rounded-2xl border border-line bg-white p-4">
              <p className="eyebrow mb-3">Labels</p>
              <ul className="space-y-2 text-xs text-ink-soft">
                {(["verified_record", "complaint_allegation", "enforcement_action", "historical_event", "uncertain", "unavailable"] as Category[]).map((c) => (
                  <li key={c} className="flex items-start gap-2" title={CATEGORY[c].help}>
                    <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${CATEGORY_DOT[c]}`} aria-hidden />
                    {CATEGORY[c].label}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </aside>

        <div className="min-w-0 space-y-14">
          <section className="space-y-5">
            <SectionHeading id="summary" eyebrow="Most important records" title="Key findings" intro="The findings that matter most, each backed by the cited records. Everything else is in the full report below." />
            {keyFindings.length > 0 ? (
              <ul className="grid gap-4 md:grid-cols-2" data-testid="key-findings">
                {keyFindings.map((f) => (
                  <SummaryItem key={f.id} f={f} />
                ))}
              </ul>
            ) : (
              <p className="card p-5 text-sm leading-relaxed text-ink-soft">
                No priority or medium-importance findings were selected. This does not mean there are no records: older and
                closed records are in the timeline and the full report below.
              </p>
            )}
            <AgentBox report={report} />
          </section>

          <section className="space-y-5">
            <SectionHeading
              id="questions"
              eyebrow="Next steps"
              title="Questions to ask"
              intro="Questions a renter could put to the landlord or broker, based on the records above."
            />
            <ol className="grid gap-3 md:grid-cols-2" data-testid="questions">
              {report.questions_to_ask.map((q, i) => (
                <li key={q} className="card flex gap-4 p-5 text-[15px] leading-relaxed text-ink">
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-brand-light font-semibold text-brand">{i + 1}</span>
                  {q}
                </li>
              ))}
            </ol>
          </section>

          <section className="space-y-5">
            <SectionHeading
              id="timeline"
              eyebrow="History"
              title="Timeline"
              intro="Dated records by year and agency. 311 requests are filed against the whole tax lot, so they appear only in the tax-lot view."
            />
            <Timeline events={report.timeline} evidence={evidence} />
          </section>

          <section className="space-y-5">
            <div className="flex flex-col gap-4 border-b border-line pb-5 xl:flex-row xl:items-end xl:justify-between">
              <div>
                <p className="eyebrow mb-1.5">Full report</p>
                <h2 id="findings" className="scroll-mt-32 font-display text-3xl font-semibold tracking-tight">Detailed findings by agency</h2>
              </div>
              <div className="no-print flex flex-wrap gap-1.5" role="group" aria-label="Filter findings by label">
                {FILTERS.map((x) => (
                  <button
                    key={x.key}
                    type="button"
                    onClick={() => setFilter(x.key)}
                    aria-pressed={filter === x.key}
                    disabled={!counts[x.key]}
                    className={`chip px-3 py-1.5 text-[13px] disabled:opacity-40 ${
                      filter === x.key ? "bg-ink text-white ring-ink" : "bg-white text-ink-soft ring-line hover:bg-paper"
                    }`}
                  >
                    {x.label}
                    <span className={filter === x.key ? "text-white/70" : "text-ink-faint"}>{counts[x.key] ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="space-y-12">
              {bodyVisible.map((s) => (
                <section key={s.id} className="space-y-4">
                  <SectionHeading id={s.id} title={s.title} intro={s.intro} />
                  <div className="space-y-3">
                    {s.finding_ids.map((id) => {
                      const f = findings.get(id);
                      return f && visible(f) ? <FindingCard key={id} f={f} evidence={evidence} /> : null;
                    })}
                  </div>
                </section>
              ))}
              {body
                .filter((s) => !bodyVisible.includes(s))
                .map((s) => (
                  <span key={s.id} id={s.id} className="block scroll-mt-32" />
                ))}
            </div>
          </section>


          <section className="space-y-5">
            <SectionHeading
              id="sources"
              eyebrow="Provenance"
              title="Sources and evidence"
              intro="Every dataset checked, whether it was fully retrieved, and the date of its last update."
            />
            <SourcesTable sources={report.sources} />
          </section>

          <section className="space-y-5">
            <SectionHeading id="method" eyebrow="Fine print" title="Limitations & method" />
            <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
              <div className="card p-6">
                <ul className="space-y-3 text-[15px] leading-relaxed text-ink-soft">
                  {report.limitations.map((l) => (
                    <li key={l} className="flex gap-3">
                      <span className="mt-2.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ink-faint" aria-hidden />
                      {l}
                    </li>
                  ))}
                </ul>
              </div>
              <div className="space-y-4">
                <div className="card p-6">
                  <p className="flex items-center gap-2 text-sm font-semibold">
                    <ShieldCheck className={`h-4 w-4 ${report.verification.passed ? "text-brand" : "text-rose-600"}`} aria-hidden />
                    Verification
                  </p>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft" data-testid="verification">
                    {report.verification.passed ? "Passed" : "Issues found"}: {report.verification.findings_checked} findings,{" "}
                    {report.verification.facts_checked} counts re-computed from the records,{" "}
                    {report.verification.citations_checked.toLocaleString()} citations checked.
                  </p>
                  {report.verification.issues.map((i, k) => (
                    <p key={k} className="mt-1 text-xs text-rose-700">
                      {i.finding_id}: {i.problem}
                    </p>
                  ))}
                </div>
                <div className="card p-6">
                  <p className="flex items-center gap-2 text-sm font-semibold">
                    <ListChecks className="h-4 w-4 text-brand" aria-hidden /> Run
                  </p>
                  <p className="mt-2 text-sm text-ink-soft">
                    {report.run.mode === "demo" ? "Demo data" : "Live NYC Open Data"} · {report.run.elapsed_seconds.toFixed(1)} s ·{" "}
                    {report.run.external_requests} API requests · {report.run.llm_calls} LLM calls
                  </p>
                  <p className="mt-1 text-xs text-ink-faint">
                    Report {report.report_id.slice(0, 8)} · generated {fmtDate(report.generated_at)} · schema {report.schema_version}
                  </p>
                </div>
              </div>
            </div>
            <p className="text-xs leading-relaxed text-ink-faint">
              {report.disclaimer} Stoop is independent and not affiliated with the City of New York.
            </p>
            <a
              href={`/corrections?${new URLSearchParams({ address: report.input_address, report: report.report_id }).toString()}`}
              className="no-print inline-flex items-center gap-1.5 text-sm font-semibold text-brand hover:text-brand-dark"
              data-testid="report-correction"
            >
              See something wrong? Report incorrect data →
            </a>
          </section>
        </div>
      </div>
    </div>
  );
}
