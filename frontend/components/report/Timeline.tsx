"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, History } from "lucide-react";
import type { Category, EvidenceItem, TimelineEvent } from "@/lib/types";
import { AGENCY_COLOR, ATTRIBUTION, fmtDate } from "@/lib/format";
import { CategoryChip } from "./ui";
import { EvidenceDetails } from "./Evidence";

type Scope = "this" | "all";
const KIND_FILTERS: { key: string; label: string; cats: Category[] | null }[] = [
  { key: "all", label: "All records", cats: null },
  { key: "verified", label: "Verified findings", cats: ["verified_record"] },
  { key: "complaints", label: "Complaints", cats: ["complaint_allegation"] },
  { key: "enforcement", label: "Enforcement & orders", cats: ["enforcement_action", "historical_event"] },
];
const PAGE = 15;

function Chart({
  events,
  filtered,
  agencies,
}: {
  events: TimelineEvent[];
  filtered: TimelineEvent[];
  agencies: string[];
}) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(760);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { years, byYear, max } = useMemo(() => {
    const ys = events.map((e) => e.year).filter(Boolean);
    const hi = Math.max(...ys);
    const lo = Math.max(Math.min(...ys), hi - 24);
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

  const W = width;
  const H = width < 500 ? 180 : 230;
  const P = { l: 34, r: 6, t: 12, b: 24 };
  const bw = (W - P.l - P.r) / years.length;
  const labelEvery = Math.max(1, Math.ceil((years.length * 34) / (W - P.l - P.r)));
  const y = (v: number) => (max ? ((H - P.t - P.b) * v) / max : 0);
  const ticks = max ? [0, Math.round(max / 2), max] : [0];

  return (
    <div className="relative" ref={box}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        className="block max-w-full"
        role="img"
        aria-label={`Records per year, ${filtered.length} shown`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={P.l} x2={W - P.r} y1={H - P.b - y(t)} y2={H - P.b - y(t)} stroke="#DDE4EF" strokeDasharray={t ? "3 4" : undefined} />
            <text x={P.l - 8} y={H - P.b - y(t) + 4} textAnchor="end" fontSize="11" fill="#5E6B80">
              {t}
            </text>
          </g>
        ))}
        {years.map((yr, i) => {
          const row = byYear.get(yr) ?? {};
          let acc = 0;
          const x = P.l + i * bw;
          return (
            <g key={yr} onMouseEnter={() => setHover(yr)} onMouseLeave={() => setHover(null)} onClick={() => setHover(yr)}>
              <rect x={x} y={P.t} width={bw} height={H - P.t - P.b} fill={hover === yr ? "#E8EEF7" : "transparent"} rx={4} />
              {agencies.map((a) => {
                const v = row[a] ?? 0;
                if (!v) return null;
                const h = y(v);
                acc += h;
                return (
                  <rect key={a} x={x + bw * 0.18} width={bw * 0.64} y={H - P.b - acc} height={Math.max(h, 1.5)} rx={2} fill={AGENCY_COLOR[a] ?? "#5E6B80"} />
                );
              })}
              {(years.length - 1 - i) % labelEvery === 0 && (
                <text x={x + bw / 2} y={H - 6} textAnchor="middle" fontSize="11" fill="#5E6B80">
                  {`’${String(yr).slice(2)}`}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {filtered.length === 0 && (
        <p className="absolute inset-0 grid place-items-center text-sm text-ink-faint">No dated records match these filters.</p>
      )}
      {hover !== null && (
        <div className="pointer-events-none absolute right-2 top-0 rounded-xl border border-line bg-white/95 px-3 py-2 text-xs shadow-lift">
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
  );
}

function EventRow({ e, ev }: { e: TimelineEvent; ev?: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  const historical = e.category === "historical_event";
  return (
    <li className="relative pl-8" data-testid="timeline-event">
      <span
        className="absolute left-[9px] top-5 h-3 w-3 -translate-x-1/2 rounded-full ring-4 ring-white"
        style={{ background: AGENCY_COLOR[e.agency] ?? "#5E6B80" }}
        aria-hidden
      />
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={!ev}
        className="w-full rounded-xl px-3 py-3 text-left transition hover:bg-paper disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-mono text-xs text-ink-faint">{fmtDate(e.date)}</span>
          <span className="text-xs font-semibold text-ink-soft">{e.agency}</span>
          <CategoryChip category={e.category} />
          {e.attribution !== "this_building" && (
            <span className="chip bg-amber-50 text-amber-900 ring-amber-300">{ATTRIBUTION[e.attribution] ?? e.attribution}</span>
          )}
          {ev && <ChevronDown className={`ml-auto h-4 w-4 text-ink-faint transition ${open ? "rotate-180" : ""}`} aria-hidden />}
        </span>
        <span className="mt-1 block text-[15px] leading-snug text-ink">{e.label}</span>
        {historical && (
          <span className="mt-1 inline-flex items-center gap-1 text-xs text-violet-800">
            <History className="h-3 w-3" aria-hidden /> Recorded on this date. Not evidence that it is in effect today.
          </span>
        )}
      </button>
      {open && ev && (
        <div className="mb-2 ml-3 rounded-xl border border-line bg-paper/50 p-4">
          <EvidenceDetails e={ev} />
        </div>
      )}
    </li>
  );
}

export function Timeline({ events, evidence }: { events: TimelineEvent[]; evidence: Map<string, EvidenceItem> }) {
  const hasBuilding = events.some((e) => e.attribution === "this_building");
  const [scope, setScope] = useState<Scope>(hasBuilding ? "this" : "all");
  const [kind, setKind] = useState("all");
  const [notableOnly, setNotableOnly] = useState(true);
  const [shown, setShown] = useState(PAGE);

  const filtered = useMemo(() => {
    const cats = KIND_FILTERS.find((k) => k.key === kind)?.cats;
    return events.filter(
      (e) => (scope === "all" || e.attribution === "this_building") && (!cats || cats.includes(e.category)),
    );
  }, [events, scope, kind]);
  const agencies = useMemo(() => Array.from(new Set(events.map((e) => e.agency))), [events]);
  const list = useMemo(
    () =>
      (notableOnly ? filtered.filter((e) => e.notable) : filtered)
        .slice()
        .sort((a, b) => b.date.localeCompare(a.date)),
    [filtered, notableOnly],
  );
  useEffect(() => setShown(PAGE), [scope, kind, notableOnly]);

  if (events.length === 0)
    return (
      <div className="card p-6 text-sm text-ink-soft" data-testid="timeline">
        No dated records were found for this address.
      </div>
    );

  const groups: [number, TimelineEvent[]][] = [];
  for (const e of list.slice(0, shown)) {
    const g = groups[groups.length - 1];
    if (g && g[0] === e.year) g[1].push(e);
    else groups.push([e.year, [e]]);
  }

  const seg = (on: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium transition ${on ? "bg-white text-ink shadow-sm ring-1 ring-line" : "text-ink-soft hover:text-ink"}`;

  return (
    <div className="card overflow-hidden" data-testid="timeline">
      <div className="no-print flex flex-col gap-3 border-b border-line p-4 sm:p-5 xl:flex-row xl:items-center xl:justify-between">
        <div className="inline-flex w-fit rounded-xl bg-paper p-1" role="group" aria-label="Which buildings">
          <button type="button" onClick={() => setScope("this")} aria-pressed={scope === "this"} className={seg(scope === "this")} disabled={!hasBuilding}>
            This building only
          </button>
          <button type="button" onClick={() => setScope("all")} aria-pressed={scope === "all"} className={seg(scope === "all")}>
            Whole tax lot (incl. 311)
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Record type">
          {KIND_FILTERS.map((k) => (
            <button
              key={k.key}
              type="button"
              onClick={() => setKind(k.key)}
              aria-pressed={kind === k.key}
              className={`chip px-3 py-1.5 text-[13px] ${kind === k.key ? "bg-ink text-white ring-ink" : "bg-white text-ink-soft ring-line hover:bg-paper"}`}
            >
              {k.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4 sm:p-6">
        <Chart events={events} filtered={filtered} agencies={agencies} />
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">
          {agencies.map((a) => (
            <span key={a} className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: AGENCY_COLOR[a] ?? "#5E6B80" }} />
              {a}
            </span>
          ))}
          <span className="ml-auto text-ink-faint">{filtered.length.toLocaleString()} dated records</span>
        </div>
      </div>

      <div className="border-t border-line p-4 sm:p-6">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="text-base font-semibold">Chronology</h3>
            <p className="text-sm text-ink-faint">
              Newest first. Select an event to see the record behind it. Past orders are shown as events, never as current status.
            </p>
          </div>
          <label className="no-print inline-flex shrink-0 cursor-pointer items-center gap-2 text-sm font-medium text-ink-soft">
            <input
              type="checkbox"
              checked={notableOnly}
              onChange={(e) => setNotableOnly(e.target.checked)}
              className="h-4 w-4 rounded border-line accent-brand"
            />
            Notable events only
          </label>
        </div>
        {list.length === 0 ? (
          <p className="mt-4 rounded-xl bg-paper px-4 py-3 text-sm text-ink-soft">
            No {notableOnly ? "notable " : ""}events match these filters.
          </p>
        ) : (
          <div className="relative mt-4" data-testid="notable-events">
            <span className="absolute bottom-2 left-[9px] top-2 w-px bg-line" aria-hidden />
            {groups.map(([yr, evs]) => (
              <div key={yr + "-" + evs[0].date + evs[0].evidence_id}>
                <p className="relative mb-1 mt-4 pl-8 font-display text-lg font-semibold first:mt-0">{yr}</p>
                <ol>
                  {evs.map((e) => (
                    <EventRow key={e.evidence_id + e.date + e.label} e={e} ev={evidence.get(e.evidence_id)} />
                  ))}
                </ol>
              </div>
            ))}
          </div>
        )}
        {list.length > shown && (
          <button type="button" onClick={() => setShown((s) => s + PAGE * 2)} className="no-print btn-ghost mt-4 w-full">
            Show more events ({(list.length - shown).toLocaleString()} more)
          </button>
        )}
      </div>
    </div>
  );
}
