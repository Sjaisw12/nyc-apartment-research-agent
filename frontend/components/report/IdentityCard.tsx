import type { ResolutionView, BuildingOverview } from "@/lib/types";
import { fmtDate } from "@/lib/format";
import { ExternalLink } from "./ui";

const CONF_CLS: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  medium: "bg-amber-50 text-amber-800 ring-amber-200",
  needs_choice: "bg-sky-50 text-sky-800 ring-sky-200",
  none: "bg-rose-50 text-rose-800 ring-rose-200",
};

const REG_LABEL: Record<string, string> = {
  on_file_end_date_not_passed: "HPD registration on file",
  on_file_end_date_passed_after_data_update: "HPD registration: status unknown",
  on_file_end_date_passed_before_data_update: "HPD registration: end date passed in this snapshot",
  on_file_end_date_passed_freshness_unknown: "HPD registration: status unknown",
  on_file_end_date_unknown: "HPD registration on file, end date unknown",
  none_on_file: "No HPD registration found in this snapshot",
};

export function IdentityCard({ r, building }: { r: ResolutionView; building?: BuildingOverview | null }) {
  const facts: [string, string][] = [];
  if (r.bbl) facts.push(["Tax lot (BBL)", r.bbl]);
  facts.push(["Building (BIN)", r.bin ?? (r.level === "tax_lot" ? "Not verified" : "—")]);
  if (building?.legal_stories != null) facts.push(["Legal stories", String(building.legal_stories)]);
  if (building?.legal_class_a_units != null) facts.push(["Class A units", String(building.legal_class_a_units)]);
  if (building?.dob_building_class) facts.push(["DOB class", building.dob_building_class]);
  if (r.footprint_bins_on_lot.length > 1) facts.push(["Buildings on lot", String(r.footprint_bins_on_lot.length)]);
  return (
    <section className="card overflow-hidden" data-testid="identity-card">
      <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-ink-faint">
            {r.level === "tax_lot" ? "Tax lot identified" : "Building identified"}
          </p>
          <h1 className="mt-1 font-display text-2xl font-semibold leading-tight sm:text-3xl">{r.label}</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-soft">{r.explanation}</p>
        </div>
        <span className={`chip shrink-0 self-start py-1 ${CONF_CLS[r.confidence]}`} data-testid="confidence">
          {r.confidence_label}
        </span>
      </div>
      <dl className="grid grid-cols-2 border-t border-stone-100 bg-stone-50/60 sm:grid-cols-3 lg:grid-cols-6">
        {facts.map(([k, v]) => (
          <div key={k} className="border-b border-r border-stone-100 px-5 py-3">
            <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{k}</dt>
            <dd className="mt-0.5 truncate font-mono text-sm text-ink" title={v}>{v}</dd>
          </div>
        ))}
      </dl>
      {(building?.registration_status || r.links.length > 0) && (
        <div className="flex flex-col gap-3 px-5 py-4 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-6">
          {building?.registration_status ? (
            <p className="text-ink-soft">
              <span className="font-medium text-ink">
                {REG_LABEL[building.registration_status] ?? "HPD registration"}
              </span>
              {building.registration_end_date && <> · end date {fmtDate(building.registration_end_date)}</>}
              {building.registration_note && <span className="block text-xs text-ink-faint">{building.registration_note}</span>}
            </p>
          ) : (
            <span />
          )}
          <div className="no-print flex flex-wrap gap-x-4 gap-y-1">
            {r.links.map((l) => (
              <ExternalLink key={l.url} href={l.url} className="text-xs">
                {l.label}
              </ExternalLink>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
