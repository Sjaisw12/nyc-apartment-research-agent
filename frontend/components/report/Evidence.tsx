"use client";

import { useState } from "react";
import type { EvidenceItem } from "@/lib/types";
import { ATTRIBUTION, AGENCY_COLOR, fmtDate, fmtSourceDate } from "@/lib/format";
import { ExternalLink } from "./ui";

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
  if (e.is_open === false) return "bg-stone-100 text-ink-soft ring-stone-200";
  return "bg-white text-ink-faint ring-stone-200";
}

export function EvidenceRow({ e }: { e: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  const details = Object.entries(e.details).filter(([, v]) => v !== null && v !== "");
  const amounts = Object.entries(e.amounts ?? {});
  return (
    <li className="border-t border-stone-100 first:border-t-0" data-testid="evidence-row" data-evidence-id={e.id}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-3 px-4 py-2.5 text-left hover:bg-stone-50 focus:outline-none focus-visible:bg-stone-50"
      >
        <span
          className="mt-1.5 h-2 w-2 shrink-0 rounded-full"
          style={{ background: AGENCY_COLOR[e.agency] ?? "#6b7690" }}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block text-sm text-ink">{e.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-faint">
            <span>{e.agency}</span>
            {e.date && (
              <span>
                {e.date_label ?? "Date"} {fmtDate(e.date)}
              </span>
            )}
            {e.status && <span className={`chip ${statusCls(e)} py-0 text-[11px]`}>{e.status}</span>}
            {e.attribution !== "this_building" && ATTRIBUTION[e.attribution] && (
              <span className="chip bg-yellow-50 py-0 text-[11px] text-yellow-900 ring-yellow-300">
                {ATTRIBUTION[e.attribution]}
              </span>
            )}
          </span>
        </span>
        <svg
          viewBox="0 0 20 20"
          className={`no-print mt-1 h-4 w-4 shrink-0 text-ink-faint transition ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden
        >
          <path d="m5 8 5 5 5-5" />
        </svg>
      </button>
      {open && (
        <div className="bg-stone-50/70 px-4 pb-4 pl-9 pt-1 text-xs">
          <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
            {details.map(([k, v]) => (
              <div key={k} className="min-w-0">
                <dt className="text-ink-faint">{k}</dt>
                <dd className="break-words text-ink">{typeof v === "boolean" ? (v ? "Yes" : "No") : String(v)}</dd>
              </div>
            ))}
            {amounts.map(([k, v]) => (
              <div key={k}>
                <dt className="text-ink-faint">{k.replace(/_/g, " ")}</dt>
                <dd className="text-ink">${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}</dd>
              </div>
            ))}
          </dl>
          {e.flags.length > 0 && (
            <ul className="mt-3 space-y-0.5 text-amber-800">
              {e.flags.map((f) => (
                <li key={f}>⚠ {FLAG_TEXT[f] ?? f.replace(/_/g, " ")}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-ink-faint">
            <span>
              {e.dataset_name} · record {e.record_id}
            </span>
            <span>Data as of {fmtSourceDate(e.data_as_of)}</span>
            {e.source_url && <ExternalLink href={e.source_url}>Source record</ExternalLink>}
            <ExternalLink href={e.dataset_url}>Dataset</ExternalLink>
          </p>
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
    <div className="overflow-hidden rounded-xl border border-stone-200 bg-white">
      <ul>
        {items.slice(0, shown).map((e) => (
          <EvidenceRow key={e.id} e={e} />
        ))}
      </ul>
      {items.length > shown && (
        <button
          type="button"
          onClick={() => setShown((s) => s + PAGE * 3)}
          className="no-print w-full border-t border-stone-100 px-4 py-2 text-xs font-medium text-brand hover:bg-stone-50"
        >
          Show more ({items.length - shown} more {label})
        </button>
      )}
    </div>
  );
}
