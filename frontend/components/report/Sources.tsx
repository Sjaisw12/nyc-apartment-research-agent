import { AlertTriangle, Ban, CalendarClock, CheckCircle2 } from "lucide-react";
import type { SourceStatus } from "@/lib/types";
import { fmtSourceDate, humanize } from "@/lib/format";
import { ExternalLink } from "./ui";

function state(s: SourceStatus) {
  if ((s.status === "records_found" || s.status === "no_records_found") && s.complete)
    return { icon: CheckCircle2, ring: "ring-brand/20", chip: "bg-brand-50 text-brand-dark ring-brand/20", icls: "text-brand" };
  if (s.status === "records_found" || s.status === "no_records_found" || s.status === "incomplete_pagination")
    return { icon: AlertTriangle, ring: "ring-amber-200", chip: "bg-amber-50 text-amber-800 ring-amber-200", icls: "text-amber-600" };
  return { icon: Ban, ring: "ring-rose-200", chip: "bg-rose-50 text-rose-800 ring-rose-200", icls: "text-rose-600" };
}

export function SourcesTable({ sources }: { sources: SourceStatus[] }) {
  return (
    <ul className="grid gap-4 md:grid-cols-2" data-testid="sources">
      {sources.map((s) => {
        const st = state(s);
        const partial = s.status === "incomplete_pagination" || (!s.complete && s.status === "records_found");
        const notChecked = st.icon === Ban;
        return (
          <li key={s.source} className="card flex flex-col p-5" data-source={s.source} data-status={s.status}>
            <div className="flex items-start gap-3">
              <st.icon className={`mt-0.5 h-5 w-5 shrink-0 ${st.icls}`} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-wide text-ink-faint">
                  {s.agency} · <span className="font-mono normal-case">{s.dataset_id}</span>
                </p>
                <ExternalLink href={s.dataset_url} className="mt-0.5 text-[15px] leading-snug">
                  {s.dataset_name}
                </ExternalLink>
              </div>
              <div className="shrink-0 text-right">
                <p className="font-display text-2xl font-semibold leading-none tabular-nums">
                  {notChecked ? "—" : `${s.records.toLocaleString()}${partial ? "+" : ""}`}
                </p>
                <p className="mt-1 text-[11px] text-ink-faint">{notChecked ? "not counted" : "records"}</p>
              </div>
            </div>
            <div className="mt-3 pl-8">
              <span className={`chip ${st.chip}`}>{s.status_label}</span>
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">{humanize(s.status_detail)}</p>
              {s.note && <p className="mt-1 text-sm text-ink-faint">{humanize(s.note)}</p>}
              {partial && (
                <p className="mt-2 text-sm text-amber-800">
                  Partial retrieval: counts from this source are lower bounds, not totals.
                </p>
              )}
              {notChecked && (
                <p className="mt-2 text-sm text-rose-800">Not checked. This is not evidence that there are no records.</p>
              )}
            </div>
            <p className="mt-auto flex items-center gap-1.5 pl-8 pt-4 text-xs text-ink-faint">
              <CalendarClock className="h-3.5 w-3.5" aria-hidden />
              {s.data_as_of ? <>Source data as of {fmtSourceDate(s.data_as_of)}</> : "Data date could not be read"}
              {s.stale && <span className="chip ml-1 bg-amber-50 text-amber-800 ring-amber-200">behind its stated update schedule</span>}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
