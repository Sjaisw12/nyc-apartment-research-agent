import type { Category, Scope } from "./types";

export const CATEGORY: Record<Category, { label: string; cls: string; help: string }> = {
  identity: { label: "Identity", cls: "bg-slate-100 text-slate-700 ring-slate-200", help: "How the building was identified." },
  verified_record: {
    label: "Verified record",
    cls: "bg-emerald-50 text-emerald-800 ring-emerald-200",
    help: "An agency's own finding, for example an HPD violation issued after an inspection.",
  },
  complaint_allegation: {
    label: "Complaint · allegation",
    cls: "bg-amber-50 text-amber-800 ring-amber-200",
    help: "Something a person reported. Not proof that the condition existed.",
  },
  enforcement_action: {
    label: "Enforcement action",
    cls: "bg-orange-50 text-orange-800 ring-orange-200",
    help: "A summons or charge an agency issued. Not by itself a finding of a violation.",
  },
  historical_event: {
    label: "Historical event",
    cls: "bg-violet-50 text-violet-800 ring-violet-200",
    help: "Something recorded on a past date, such as an order. Not proof it applies today.",
  },
  administrative_record: {
    label: "Administrative record",
    cls: "bg-sky-50 text-sky-800 ring-sky-200",
    help: "What an agency has on file. Not proof of ownership, management or legal status.",
  },
  uncertain: {
    label: "Uncertain",
    cls: "bg-stone-100 text-stone-700 ring-stone-300",
    help: "The data is partial, conflicting, or cannot settle the question.",
  },
  unavailable: {
    label: "Unavailable",
    cls: "bg-rose-50 text-rose-800 ring-rose-200",
    help: "Could not be checked. This is not evidence that there are no records.",
  },
};

export const SCOPE: Record<Scope, string> = {
  this_building: "This building",
  lot_level: "Whole tax lot",
  other_building_on_lot: "Another building on the lot",
  unattributed_on_lot: "On the lot, building unknown",
  mixed: "Mixed buildings",
  not_applicable: "",
};

export const ATTRIBUTION: Record<string, string> = {
  this_building: "This building",
  lot_level: "Tax lot",
  other_building_on_lot: "Other building on lot",
  unattributed_on_lot: "Lot, building unknown",
  mixed: "Mixed",
};

export const AGENCY_COLOR: Record<string, string> = {
  HPD: "#1f5f5b",
  DOB: "#c2410c",
  "DOB / ECB": "#7c3aed",
  "311": "#2563eb",
};

export function fmtDate(iso?: string | null) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function fmtSourceDate(raw?: string | null) {
  if (!raw) return "unknown";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}
