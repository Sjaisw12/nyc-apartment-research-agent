"use client";

import { useState } from "react";
import type { EvidenceItem, Finding } from "@/lib/types";
import { CategoryChip, ScopeChip } from "./ui";
import { EvidenceList } from "./Evidence";

const IMPORTANCE: Record<Finding["importance"], string> = {
  high: "border-l-amber-500",
  medium: "border-l-brand/60",
  info: "border-l-stone-200",
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
  return (
    <article
      id={`finding-${f.id}`}
      className={`card scroll-mt-24 border-l-4 ${IMPORTANCE[f.importance]} p-4 sm:p-5`}
      data-testid="finding"
      data-finding-id={f.id}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <CategoryChip category={f.category} />
        <ScopeChip scope={f.scope} />
        {f.importance === "high" && (
          <span className="chip bg-amber-500/10 text-amber-900 ring-amber-300">Worth asking about</span>
        )}
        <span className="ml-auto font-mono text-[11px] text-ink-faint">{f.id}</span>
      </div>
      <h3 className="mt-2 text-base font-semibold leading-snug text-ink">{f.title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-ink-soft">{f.statement}</p>
      {f.caveat && (
        <p className="mt-2 rounded-lg bg-stone-50 px-3 py-2 text-xs leading-relaxed text-ink-soft">
          <span className="font-semibold text-ink">Caveat: </span>
          {f.caveat}
        </p>
      )}
      {items.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="no-print inline-flex items-center gap-1 text-xs font-semibold text-brand hover:underline"
            data-testid="toggle-evidence"
          >
            {open ? "Hide" : "Show"} {items.length} cited record{items.length === 1 ? "" : "s"}
            <svg viewBox="0 0 20 20" className={`h-3.5 w-3.5 transition ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
              <path d="m5 8 5 5 5-5" />
            </svg>
          </button>
          <p className="hidden text-xs text-ink-faint print:block">
            Cites {items.length} record{items.length === 1 ? "" : "s"}: {items.slice(0, 6).map((e) => e.record_id).join(", ")}
            {items.length > 6 ? ", …" : ""}
          </p>
          {open && (
            <div className="mt-2">
              <EvidenceList items={items} />
            </div>
          )}
        </div>
      )}
    </article>
  );
}
