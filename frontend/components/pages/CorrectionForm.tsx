"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, Loader2 } from "lucide-react";
import { ApiError, submitCorrection, type CorrectionInput } from "@/lib/api";

const KINDS: [CorrectionInput["kind"], string][] = [
  ["wrong_building", "A record is shown for the wrong building"],
  ["record_incorrect", "A record or count looks incorrect"],
  ["record_outdated", "A status is out of date"],
  ["privacy", "Something should not be shown (privacy)"],
  ["other", "Something else"],
];

export function CorrectionForm() {
  const q = useSearchParams();
  const [kind, setKind] = useState<CorrectionInput["kind"]>("record_incorrect");
  const [address, setAddress] = useState(q.get("address") ?? "");
  const [recordId, setRecordId] = useState(q.get("record") ?? "");
  const [details, setDetails] = useState("");
  const [state, setState] = useState<{ s: "idle" | "busy" | "done" | "error"; msg?: string }>({ s: "idle" });
  const reportId = q.get("report");

  if (state.s === "done")
    return (
      <div className="not-prose flex gap-3 rounded-2xl border border-brand/30 bg-brand-50 p-5 text-ink" role="status" data-testid="correction-done">
        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-brand" aria-hidden />
        <div>
          <p className="font-semibold">Thanks, your report was received.</p>
          <p className="mt-1 text-sm text-ink-soft">Reference {state.msg}. It will be reviewed manually.</p>
        </div>
      </div>
    );

  return (
    <form
      className="not-prose card space-y-5 p-6"
      data-testid="correction-form"
      onSubmit={async (e) => {
        e.preventDefault();
        setState({ s: "busy" });
        try {
          const r = await submitCorrection({
            kind,
            address: address.trim(),
            report_id: reportId && /^[0-9a-f]{6,32}$/.test(reportId) ? reportId : null,
            record_id: recordId.trim() || null,
            details: details.trim(),
          });
          setState({ s: "done", msg: r.reference });
        } catch (err) {
          setState({ s: "error", msg: err instanceof ApiError ? err.message : "Something went wrong. Please try again." });
        }
      }}
    >
      <fieldset>
        <legend className="text-sm font-semibold text-ink">What is the problem?</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {KINDS.map(([k, label]) => (
            <label key={k} className={`flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 text-sm ${kind === k ? "border-brand bg-brand-50" : "border-line bg-white"}`}>
              <input type="radio" name="kind" value={k} checked={kind === k} onChange={() => setKind(k)} className="mt-0.5 accent-[#2563EB]" />
              {label}
            </label>
          ))}
        </div>
      </fieldset>
      <div>
        <label htmlFor="c-address" className="text-sm font-semibold text-ink">Building address</label>
        <input id="c-address" required maxLength={200} value={address} onChange={(e) => setAddress(e.target.value)} className="mt-1.5 w-full rounded-xl border border-line px-3.5 py-2.5 text-[15px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
      </div>
      <div>
        <label htmlFor="c-record" className="text-sm font-semibold text-ink">Record reference <span className="font-normal text-ink-faint">(optional)</span></label>
        <input id="c-record" maxLength={80} value={recordId} onChange={(e) => setRecordId(e.target.value)} placeholder="e.g. HPD violation 9666546" className="mt-1.5 w-full rounded-xl border border-line px-3.5 py-2.5 text-[15px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
      </div>
      <div>
        <label htmlFor="c-details" className="text-sm font-semibold text-ink">What is wrong?</label>
        <textarea id="c-details" required minLength={5} maxLength={1500} rows={5} value={details} onChange={(e) => setDetails(e.target.value)} className="mt-1.5 w-full rounded-xl border border-line px-3.5 py-2.5 text-[15px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
        <p className="mt-1 text-xs text-ink-faint">Please leave out names and contact details.</p>
      </div>
      {reportId && <p className="text-xs text-ink-faint">Linked to report {reportId.slice(0, 8)}.</p>}
      {state.s === "error" && <p role="alert" className="text-sm text-rose-700">{state.msg}</p>}
      <button type="submit" className="btn-primary" disabled={state.s === "busy"}>
        {state.s === "busy" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
        Send correction request
      </button>
    </form>
  );
}
