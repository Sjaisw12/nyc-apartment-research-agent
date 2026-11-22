"use client";

import { useMemo, useState } from "react";
import type { Category, TimelineEvent } from "@/lib/types";
import { AGENCY_COLOR, ATTRIBUTION, fmtDate } from "@/lib/format";
import { CategoryChip } from "./ui";

type Scope = "this" | "all";
const KIND_FILTERS: { key: string; label: string; cats: Category[] | null }[] = [
  { key: "all", label: "All records", cats: null },
  { key: "verified", label: "Verified findings", cats: ["verified_record"] },
  { key: "complaints", label: "Complaints", cats: ["complaint_allegation"] },
  { key: "enforcement", label: "Enforcement & orders", cats: ["enforcement_action", "historical_event"] },
];

export function Timeline({ events }: { events: TimelineEvent[] }) {
  const [scope, setScope] = useState<Scope>("this");
  const [kind, setKind] = useState("all");
  const [hover, setHover] = useState<number | null>(null);

  const filtered = useMemo(() => {
    const cats = KIND_FILTERS.find((k) => k.key === kind)?.cats;
    return events.filter(
      (e) => (scope === "all" || e.attribution === "this_building") && (!cats || cats.includes(e.category)),
    );
  }, [events, scope, kind]);

  const agencies = useMemo(() => Array.from(new Set(events.map((e) => e.agency))), [events]);
  const { years, byYear, max } = useMemo(() => {
    const ys = events.map((e) => e.year).filter(Boolean);
    if (ys.length === 0) return { years: [] as number[], byYear: new Map<number, Record<string, number>>(), max: 0 };
    const lo = Math.max(Math.min(...ys), Math.max(...ys) - 24);
    const hi = Math.max(...ys);
    const years = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
    const byYear = new Map<number, Record<string, number>>(years.map((y) => [y, {}]));
    for (const e of filtered) {
      const row = byYear.get(e.year);
      if (row) row[e.agency] = (row[e.agency] ?? 0) + 1;
    }
    let max = 0;
    byYear.forEach((r) => (max = Math.max(max, Object.values(r).reduce((a, b) => a + b, 0))));
    return { years, byYear, max };
  }, [events, filtered]);

  const notable = useMemo(
    () => filtered.filter((e) => e.notable).sort((a, b) => b.date.localeCompare(a.date)).slice(0, 12),
    [filtered],
  );
  const earlier = events.length ? Math.min(...events.map((e) => e.year)) : null;

  if (events.length === 0)
    return <p className="text-sm text-ink-soft">No dated records were found for this address.</p>;

  const W = 720, H = 200, P = { l: 34, r: 8, t: 10, b: 24 };
  const bw = (W - P.l - P.r) / years.length;
  const yScale = (v: number) => (max ? ((H - P.t - P.b) * v) / max : 0);
  const ticks = max ? [0, Math.round(max / 2), max] : [0];

  return (
    <div className="card p-4 sm:p-5" data-testid="timeline">
      <div className="no-print flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="inline-flex rounded-lg border border-stone-200 bg-stone-50 p-0.5 text-xs font-medium" role="group" aria-label="Which buildings">
          {(["this", "all"] as Scope[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setScope(s)}
              aria-pressed={scope === s}
              className={`rounded-md px-3 py-1.5 ${scope === s ? "bg-white text-ink shadow-sm" : "text-ink-soft hover:text-ink"}`}
            >
              {s === "this" ? "This building only" : "Whole tax lot (incl. 311)"}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Record type">
          {KIND_FILTERS.map((k) => (
            <button
              key={k.key}
              type="button"
              onClick={() => setKind(k.key)}
              aria-pressed={kind === k.key}
              className={`chip py-1 ${kind === k.key ? "bg-ink text-white ring-ink" : "bg-white text-ink-soft ring-stone-200 hover:bg-stone-50"}`}
            >
              {k.label}
            </button>
          ))}
        </div>
      </div>

      <div className="relative mt-4">
        <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Records per year, ${filtered.length} shown`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={P.l} x2={W - P.r} y1={H - P.b - yScale(t)} y2={H - P.b - yScale(t)} stroke="#e7e5e4" />
              <text x={P.l - 6} y={H - P.b - yScale(t) + 3} textAnchor="end" fontSize="10" fill="#6b7690">
                {t}
              </text>
            </g>
          ))}
          {years.map((y, i) => {
            const row = byYear.get(y) ?? {};
            let acc = 0;
            const x = P.l + i * bw;
            return (
              <g key={y} onMouseEnter={() => setHover(y)} onMouseLeave={() => setHover(null)}>
                <rect x={x} y={P.t} width={bw} height={H - P.t - P.b} fill={hover === y ? "#f5f5f4" : "transparent"} />
                {agencies.map((a) => {
                  const v = row[a] ?? 0;
                  if (!v) return null;
                  const h = yScale(v);
                  acc += h;
                  return (
                    <rect key={a} x={x + bw * 0.15} width={bw * 0.7} y={H - P.b - acc} height={Math.max(h, 1)} rx={1.5} fill={AGENCY_COLOR[a] ?? "#6b7690"} />
                  );
                })}
                {(years.length <= 14 || i % 2 === (years.length - 1) % 2) && (
                  <text x={x + bw / 2} y={H - 8} textAnchor="middle" fontSize="10" fill="#6b7690">
                    {`’${String(y).slice(2)}`}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="pointer-events-none absolute right-2 top-0 rounded-lg border border-stone-200 bg-white/95 px-3 py-2 text-xs shadow-card">
            <p className="font-semibold">{hover}</p>
            {agencies.map((a) => (
              <p key={a} className="flex items-center gap-1.5 text-ink-soft">
                <span className="h-2 w-2 rounded-full" style={{ background: AGENCY_COLOR[a] }} />
                {a}: {byYear.get(hover)?.[a] ?? 0}
              </p>
            ))}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">
        {agencies.map((a) => (
          <span key={a} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: AGENCY_COLOR[a] ?? "#6b7690" }} />
            {a}
          </span>
        ))}
        <span className="ml-auto text-ink-faint">
          {filtered.length.toLocaleString()} dated records shown
          {earlier !== null && years.length && earlier < years[0] ? ` · chart starts ${years[0]}` : ""}
        </span>
      </div>

      {notable.length > 0 && (
        <div className="mt-5">
          <h3 className="text-sm font-semibold">Notable events</h3>
          <p className="text-xs text-ink-faint">
            Serious violations, orders and enforcement actions, newest first. A past order is a historical event, not proof it applies today.
          </p>
          <ol className="mt-2 divide-y divide-stone-100 rounded-xl border border-stone-200" data-testid="notable-events">
            {notable.map((e) => (
              <li key={e.evidence_id + e.date + e.label} className="flex flex-col gap-1 px-3 py-2 text-sm sm:flex-row sm:items-center sm:gap-3">
                <span className="w-28 shrink-0 font-mono text-xs text-ink-faint">{fmtDate(e.date)}</span>
                <span className="min-w-0 flex-1 text-ink">{e.label}</span>
                <span className="flex shrink-0 flex-wrap gap-1">
                  <CategoryChip category={e.category} />
                  {e.attribution !== "this_building" && (
                    <span className="chip bg-yellow-50 text-yellow-900 ring-yellow-300">{ATTRIBUTION[e.attribution] ?? e.attribution}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
