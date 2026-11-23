"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown } from "lucide-react";
import type { EvidenceItem } from "@/lib/types";
import { ATTRIBUTION, AGENCY_COLOR, fmtDate, fmtSourceDate } from "@/lib/format";
import { ExternalLink } from "./ui";
import { GLOSSARY } from "@/lib/glossary";

const FLAG_TEXT: Record<string, string> = {
  unit_detail_redacted: "Apartment or floor detail removed from the description for privacy",
  bin_single_source: "The building number on this record is confirmed by one source only",
  date_order: "The dates on this record are out of order",
  complaint_category_unknown: "The complaint category code is not in DOB's published list",
  severity_undocumented_value: "The severity value is not documented by the agency",
  uncommon_value_withheld: "An uncommon free-text value was withheld",
  disposition_code_unknown: "The disposition code is not in DOB's published list",
};

function statusCls(e: EvidenceItem) {
  if (e.is_open === true) return "bg-amber-50 text-amber-800 ring-amber-200";
  if (e.is_open === false) return "bg-slate-100 text-ink-soft ring-slate-200";
  return "bg-white text-ink-faint ring-line";
}

export function EvidenceDetails({ e }: { e: EvidenceItem }) {
  const details = Object.entries(e.details).filter(([, v]) => v !== null && v !== "");
  const amounts = Object.entries(e.amounts ?? {});
  return (
    <div className="text-sm">
      <dl className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
        {details.map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="text-xs text-ink-faint">{k}</dt>
            <dd className="break-words text-ink">{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
          </div>
        ))}
        {amounts.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs capitalize text-ink-faint">{k.replace(/_/g, " ")}</dt>
            <dd className="text-ink">${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}</dd>
          </div>
        ))}
      </dl>
      {e.flags.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-amber-800">
          {e.flags.map((f) => (
            <li key={f} className="flex gap-1.5">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
              {FLAG_TEXT[f] ?? f.replace(/_/g, " ")}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-3 text-xs text-ink-faint">
        <span>
          {e.agency} · {e.dataset_name} · record {e.record_id}
        </span>
        <span>Data as of {fmtSourceDate(e.data_as_of)}</span>
        {e.source_url && <ExternalLink href={e.source_url}>Source record</ExternalLink>}
        <ExternalLink href={e.dataset_url}>Dataset</ExternalLink>
      </p>
    </div>
  );
}

export function EvidenceRow({ e }: { e: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-t border-line first:border-t-0" data-testid="evidence-row" data-evidence-id={e.id}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-paper/70"
      >
        <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: AGENCY_COLOR[e.agency] ?? "#5E6B80" }} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] text-ink">{e.title}</span>
          <span className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-ink-faint">
            <span className="font-semibold text-ink-soft">{e.agency}</span>
            {e.date && (
              <span>
                {e.date_label ?? "Date"} {fmtDate(e.date)}
              </span>
            )}
            {e.status && (
              <span className={`chip ${statusCls(e)} max-w-full truncate py-0 text-[11px]`} title={e.is_open === false ? GLOSSARY.Closed.short : undefined}>
                {e.status}
              </span>
            )}
            {e.attribution !== "this_building" && ATTRIBUTION[e.attribution] && (
              <span className="chip bg-amber-50 py-0 text-[11px] text-amber-900 ring-amber-300">{ATTRIBUTION[e.attribution]}</span>
            )}
          </span>
        </span>
        <ChevronDown className={`no-print mt-1 h-4 w-4 shrink-0 text-ink-faint transition ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <div className="bg-paper/60 px-4 pb-4 pl-9 pt-2">
          <EvidenceDetails e={e} />
        </div>
      )}
    </li>
  );
}

const PAGE = 8;

export function EvidenceList({ items, label = "records" }: { items: EvidenceItem[]; label?: string }) {
  const [shown, setShown] = useState(PAGE);
  if (items.length === 0) return null;
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-white">
      <ul>
        {items.slice(0, shown).map((e) => (
          <EvidenceRow key={e.id} e={e} />
        ))}
      </ul>
      {items.length > shown && (
        <button
          type="button"
          onClick={() => setShown((s) => s + PAGE * 3)}
          className="no-print w-full border-t border-line px-4 py-2.5 text-sm font-semibold text-brand hover:bg-paper"
        >
          Show more ({(items.length - shown).toLocaleString()} more {label})
        </button>
      )}
    </div>
  );
}
