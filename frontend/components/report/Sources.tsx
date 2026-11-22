import type { SourceStatus } from "@/lib/types";
import { fmtSourceDate } from "@/lib/format";
import { ExternalLink } from "./ui";

function tone(s: SourceStatus) {
  if (s.status === "records_found" || s.status === "no_records_found")
    return s.complete ? "bg-emerald-50 text-emerald-800 ring-emerald-200" : "bg-amber-50 text-amber-800 ring-amber-200";
  if (s.status === "incomplete_pagination") return "bg-amber-50 text-amber-800 ring-amber-200";
  return "bg-rose-50 text-rose-800 ring-rose-200";
}

export function SourcesTable({ sources }: { sources: SourceStatus[] }) {
  return (
    <div className="card overflow-hidden" data-testid="sources">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="bg-stone-50 text-xs uppercase tracking-wide text-ink-faint">
            <tr>
              <th className="px-4 py-2.5 font-medium">Dataset</th>
              <th className="px-4 py-2.5 font-medium">Status</th>
              <th className="px-4 py-2.5 text-right font-medium">Records</th>
              <th className="px-4 py-2.5 font-medium">Data as of</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-stone-100">
            {sources.map((s) => (
              <tr key={s.source} className="align-top" data-source={s.source} data-status={s.status}>
                <td className="px-4 py-3">
                  <ExternalLink href={s.dataset_url} className="font-medium">
                    {s.dataset_name}
                  </ExternalLink>
                  <span className="block text-xs text-ink-faint">
                    {s.agency} · {s.dataset_id}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`chip ${tone(s)}`}>{s.status_label}</span>
                  <span className="mt-1 block text-xs text-ink-soft">{s.status_detail}</span>
                  {s.note && <span className="mt-0.5 block text-xs text-ink-faint">{s.note}</span>}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">
                  {s.records.toLocaleString()}
                  {s.status === "incomplete_pagination" ? "+" : ""}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-xs text-ink-soft">
                  {fmtSourceDate(s.data_as_of)}
                  {s.stale && <span className="ml-1 chip bg-amber-50 text-amber-800 ring-amber-200">stale</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
