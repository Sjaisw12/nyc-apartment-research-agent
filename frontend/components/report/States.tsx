"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ResearchReport } from "@/lib/types";
import { FALLBACK_EXAMPLES } from "@/lib/api";
import { ExternalLink } from "./ui";

const STEPS = [
  "Matching the address to a tax lot and building",
  "Checking HPD violations and complaints",
  "Checking DOB complaints and ECB summonses",
  "Checking 311 requests and HPD registration",
  "Building cited findings and checking every number",
];

export function Loading({ address }: { address: string }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((x) => Math.min(x + 1, STEPS.length - 1)), 1400);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="card mx-auto max-w-xl p-6" role="status" aria-live="polite" data-testid="loading">
      <p className="text-xs font-semibold uppercase tracking-wider text-ink-faint">Researching</p>
      <p className="mt-1 font-display text-xl font-semibold">{address}</p>
      <ol className="mt-5 space-y-2.5">
        {STEPS.map((s, k) => (
          <li key={s} className={`flex items-center gap-3 text-sm ${k <= i ? "text-ink" : "text-ink-faint"}`}>
            <span
              className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[10px] ${
                k < i ? "bg-brand text-white" : k === i ? "border-2 border-brand border-t-transparent animate-spin" : "border border-stone-300"
              }`}
            >
              {k < i ? "✓" : ""}
            </span>
            {s}
          </li>
        ))}
      </ol>
      <p className="mt-5 text-xs text-ink-faint">Usually under a second with demo data, and about 10 seconds against live NYC Open Data.</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card mx-auto max-w-xl border-rose-200 p-6" role="alert" data-testid="error">
      <p className="font-display text-xl font-semibold">No report this time</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{message}</p>
      <div className="mt-4 flex gap-2">
        {onRetry && (
          <button type="button" className="btn-primary" onClick={onRetry}>
            Try again
          </button>
        )}
        <Link href="/" className="btn-ghost">
          New search
        </Link>
      </div>
    </div>
  );
}

export function Ambiguous({ report, onPick }: { report: ResearchReport; onPick: (bbl: string, bin: string | null) => void }) {
  const r = report.resolution;
  return (
    <div className="card mx-auto max-w-2xl p-6" data-testid="ambiguous">
      <p className="text-xs font-semibold uppercase tracking-wider text-sky-700">More than one match</p>
      <h1 className="mt-1 font-display text-2xl font-semibold">Which building do you mean?</h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{r.explanation}</p>
      <p className="mt-1 text-sm text-ink-soft">
        We won't guess, because records for the wrong building would be misleading. Pick one, or search again with the borough or ZIP code.
      </p>
      <ul className="mt-4 space-y-2">
        {r.options.map((o) => (
          <li key={`${o.bbl}-${o.bin}`}>
            <button
              type="button"
              onClick={() => onPick(o.bbl, o.bin)}
              className="flex w-full items-center justify-between gap-3 rounded-xl border border-stone-200 bg-white px-4 py-3 text-left transition hover:border-brand hover:bg-brand-light/40"
              data-testid="candidate"
            >
              <span>
                <span className="block font-medium text-ink">{o.label}</span>
                <span className="block font-mono text-xs text-ink-faint">
                  {o.borough} · BBL {o.bbl}
                  {o.bin ? ` · BIN ${o.bin}` : ""}
                </span>
              </span>
              <span className="text-brand">→</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function NoMatch({ report }: { report: ResearchReport }) {
  const r = report.resolution;
  const unavailable = r.outcome === "source_unavailable";
  return (
    <div className="card mx-auto max-w-2xl p-6" data-testid={unavailable ? "unavailable" : "no-match"}>
      <p className={`text-xs font-semibold uppercase tracking-wider ${unavailable ? "text-rose-700" : "text-ink-faint"}`}>
        {unavailable ? "Address service unavailable" : "No reliable match"}
      </p>
      <h1 className="mt-1 font-display text-2xl font-semibold">
        {unavailable ? "We couldn't check this address right now" : "We couldn't confidently identify this building"}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">{r.explanation}</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-soft">
        {unavailable
          ? "The city's address service did not respond, so no records were checked. This says nothing about the building."
          : "Rather than show records that may belong to a different building, no report was produced. Check the spelling, add the borough or ZIP code, or try an example."}
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {FALLBACK_EXAMPLES.map((e) => (
          <Link key={e.key} href={`/investigate?address=${encodeURIComponent(e.address)}`} className="chip bg-white py-1 text-ink-soft ring-stone-200 hover:bg-stone-50">
            {e.address}
          </Link>
        ))}
      </div>
      {r.links.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-4 text-xs">
          {r.links.map((l) => (
            <ExternalLink key={l.url} href={l.url}>
              {l.label}
            </ExternalLink>
          ))}
        </div>
      )}
    </div>
  );
}
