"use client";

import { useState } from "react";
import { ChevronDown, Info } from "lucide-react";
import type { EvidenceItem, Finding } from "@/lib/types";
import { CATEGORY_BAR, CATEGORY_ICON, CategoryChip, ScopeChip } from "./ui";
import { EvidenceList } from "./Evidence";

const ICON_TONE: Record<string, string> = {
  verified_record: "bg-brand-50 text-brand",
  complaint_allegation: "bg-amber-50 text-amber-700",
  enforcement_action: "bg-orange-50 text-orange-700",
  historical_event: "bg-violet-50 text-violet-700",
  administrative_record: "bg-sky-50 text-sky-700",
  uncertain: "bg-slate-100 text-slate-600",
  unavailable: "bg-rose-50 text-rose-700",
  identity: "bg-slate-100 text-slate-700",
};

export function FindingCard({
  f,
  evidence,
  defaultOpen = false,
}: {
  f: Finding;
  evidence: Map<string, EvidenceItem>;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const items = f.evidence_ids.map((id) => evidence.get(id)).filter(Boolean) as EvidenceItem[];
  const Icon = CATEGORY_ICON[f.category];
  const agencies = Array.from(new Set(items.map((e) => e.agency)));
  return (
    <article
      id={`finding-${f.id}`}
      className={`card scroll-mt-32 border-l-4 ${CATEGORY_BAR[f.category]} lg:scroll-mt-24`}
      data-testid="finding"
      data-finding-id={f.id}
      data-category={f.category}
    >
      <div className="flex gap-4 p-5 sm:p-6">
        <span className={`hidden h-10 w-10 shrink-0 place-items-center rounded-xl sm:grid ${ICON_TONE[f.category]}`}>
          <Icon className="h-5 w-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <CategoryChip category={f.category} />
            <ScopeChip scope={f.scope} />
            {f.importance === "high" && (
              <span className="chip bg-caution-light text-caution ring-caution/30">Worth asking about</span>
            )}
          </div>
          <h3 className="mt-2.5 text-[17px] font-semibold leading-snug text-ink">{f.title}</h3>
          <p className="mt-1.5 text-[15px] leading-relaxed text-ink-soft">{f.statement}</p>
          {f.caveat && (
            <p className="mt-3 flex gap-2 rounded-xl bg-paper px-3.5 py-2.5 text-sm leading-relaxed text-ink-soft">
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-faint" aria-hidden />
              <span>{f.caveat}</span>
            </p>
          )}
          {items.length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
              <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
                aria-controls={`ev-${f.id}`}
                className="no-print inline-flex items-center gap-1.5 rounded-lg bg-brand-50 px-3 py-1.5 text-sm font-semibold text-brand ring-1 ring-brand/15 hover:bg-brand-light"
                data-testid="toggle-evidence"
              >
                {open ? "Hide" : "View"} {items.length.toLocaleString()} cited record{items.length === 1 ? "" : "s"}
                <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} aria-hidden />
              </button>
              <span className="text-xs text-ink-faint">
                Source: {agencies.join(", ")} · {items[0].dataset_name}
                {new Set(items.map((e) => e.dataset_id)).size > 1 ? " and others" : ""}
              </span>
              <span className="font-mono text-[11px] text-ink-faint/80">{f.id}</span>
            </div>
          )}
          <p className="hidden text-xs text-ink-faint print:block">
            Cites {items.length} record{items.length === 1 ? "" : "s"}:{" "}
            {items
              .slice(0, 6)
              .map((e) => e.record_id)
              .join(", ")}
            {items.length > 6 ? ", …" : ""}
          </p>
        </div>
      </div>
      {open && (
        <div id={`ev-${f.id}`} className="border-t border-line bg-paper/40 p-3 sm:p-4">
          <EvidenceList items={items} />
        </div>
      )}
    </article>
  );
}
