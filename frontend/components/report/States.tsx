"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, Building2, CircleHelp, CloudOff, Loader2, MapPinOff, RotateCcw, SearchX } from "lucide-react";
import type { ResearchReport } from "@/lib/types";
import { EXAMPLES, investigateHref } from "@/lib/examples";
import { ExternalLink } from "./ui";

const STEPS = [
  "Matching the address to a tax lot and building",
  "Checking HPD violations and complaints",
  "Checking DOB complaints and ECB summonses",
  "Checking 311 requests and HPD registration",
  "Building cited findings and re-checking every number",
];

export function Loading({ address, live }: { address: string; live?: boolean }) {
  const [t, setT] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setT((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const per = live ? 2.2 : 0.5;
  const i = Math.min(Math.floor(t / per), STEPS.length - 1);
  return (
    <div className="container-site py-12 sm:py-16">
      <div className="card mx-auto max-w-2xl overflow-hidden" role="status" aria-live="polite" data-testid="loading">
        <div className="h-1 bg-paper">
          <div
            className="h-1 bg-brand transition-all duration-1000"
            style={{ width: `${Math.min(95, ((t + 1) / (STEPS.length * per + 2)) * 100)}%` }}
          />
        </div>
        <div className="p-6 sm:p-8">
          <p className="eyebrow">Researching {live ? "live NYC Open Data" : "recorded demo data"}</p>
          <p className="mt-2 font-display text-2xl font-semibold leading-snug sm:text-3xl">{address}</p>
          <ol className="mt-6 space-y-3">
            {STEPS.map((s, k) => (
              <li key={s} className={`flex items-center gap-3 text-[15px] ${k <= i ? "text-ink" : "text-ink-faint"}`}>
                <span
                  className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs ${
                    k < i ? "bg-brand text-white" : k === i ? "text-brand" : "border border-line"
                  }`}
                >
                  {k < i ? "✓" : k === i ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : ""}
                </span>
                {s}
              </li>
            ))}
          </ol>
          <p className="mt-6 text-sm text-ink-faint">
            {live
              ? t < 25
                ? "Live reports query the city's datasets directly and usually take 5 to 15 seconds."
                : "NYC Open Data is responding slowly. Stoop waits up to 90 seconds before giving up on a source, and will tell you which sources it could not check."
              : "Demo reports load in about a second."}
            <span className="ml-1 tabular-nums">({t}s)</span>
          </p>
        </div>
      </div>
    </div>
  );
}

function Shell({
  icon: Icon,
  tone,
  eyebrow,
  title,
  children,
  testid,
}: {
  icon: typeof SearchX;
  tone: string;
  eyebrow: string;
  title: string;
  children: React.ReactNode;
  testid: string;
}) {
  return (
    <div className="container-site py-12 sm:py-16">
      <div className="card mx-auto max-w-3xl p-6 sm:p-10" data-testid={testid}>
        <span className={`grid h-12 w-12 place-items-center rounded-2xl ${tone}`}>
          <Icon className="h-6 w-6" aria-hidden />
        </span>
        <p className="eyebrow mt-6">{eyebrow}</p>
        <h1 className="mt-2 font-display text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">{title}</h1>
        <div className="mt-4 space-y-4 text-[15px] leading-relaxed text-ink-soft">{children}</div>
      </div>
    </div>
  );
}

function ExampleLinks() {
  return (
    <div>
      <p className="mb-2 text-sm font-semibold text-ink">Try a recorded example</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {EXAMPLES.map((e) => (
          <Link
            key={e.key}
            href={investigateHref(e.address)}
            className="group flex items-center justify-between gap-3 rounded-xl border border-line bg-paper/40 px-4 py-3 hover:border-brand/40 hover:bg-white"
          >
            <span>
              <span className="block text-sm font-semibold text-ink">{e.title}</span>
              <span className="block text-xs text-ink-faint">
                {e.neighborhood}, {e.borough}
              </span>
            </span>
            <ArrowRight className="h-4 w-4 text-ink-faint group-hover:text-brand" aria-hidden />
          </Link>
        ))}
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Shell icon={CloudOff} tone="bg-rose-50 text-rose-700" eyebrow="No report this time" title="Something went wrong" testid="error">
      <p>{message}</p>
      <div className="flex flex-wrap gap-2 pt-2">
        {onRetry && (
          <button type="button" className="btn-primary" onClick={onRetry}>
            <RotateCcw className="h-4 w-4" aria-hidden /> Try again
          </button>
        )}
        <Link href="/" className="btn-ghost">
          New search
        </Link>
      </div>
    </Shell>
  );
}

export function EmptyState() {
  return (
    <Shell icon={SearchX} tone="bg-paper text-ink-soft" eyebrow="No address" title="Enter an NYC street address to start" testid="error">
      <p>Use the search box above, for example “1134 West Farms Rd, Bronx” or “350 5th Ave, Manhattan”.</p>
      <ExampleLinks />
    </Shell>
  );
}

export function Ambiguous({ report, onPick }: { report: ResearchReport; onPick: (bbl: string, bin: string | null) => void }) {
  const r = report.resolution;
  const single = r.options.length === 1;
  return (
    <Shell
      icon={CircleHelp}
      tone="bg-sky-50 text-sky-700"
      eyebrow={single ? "Please confirm" : "More than one match"}
      title={single ? "Did you mean this building?" : "Which building do you mean?"}
      testid="ambiguous"
    >
      <p>{r.explanation}</p>
      <p>
        {single
          ? "Stoop found a likely match but could not confirm it automatically. If this is the right building, select it to see the report; otherwise check the address and search again."
          : "Stoop won’t guess, because records for the wrong building would be misleading. Pick the right one, or search again with the borough or ZIP code."}
      </p>
      <ul className="grid gap-3 pt-2 sm:grid-cols-2">
        {r.options.map((o) => (
          <li key={`${o.bbl}-${o.bin}`}>
            <button
              type="button"
              onClick={() => onPick(o.bbl, o.bin)}
              className="group flex h-full w-full items-start gap-3 rounded-2xl border border-line bg-white p-4 text-left transition hover:border-brand hover:shadow-lift"
              data-testid="candidate"
            >
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-light text-brand">
                <Building2 className="h-5 w-5" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block font-semibold text-ink group-hover:text-brand">{o.borough}</span>
                <span className="block text-sm text-ink-soft">{o.label}</span>
                <span className="mt-1 block font-mono text-xs text-ink-faint">
                  BBL {o.bbl}
                  {o.bin ? ` · BIN ${o.bin}` : ""}
                </span>
              </span>
              <ArrowRight className="mt-3 h-4 w-4 shrink-0 text-ink-faint group-hover:text-brand" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </Shell>
  );
}

export function NoMatch({ report }: { report: ResearchReport }) {
  const r = report.resolution;
  const demo = report.run.mode === "demo";
  if (r.outcome === "source_unavailable" && demo && !r.in_demo_dataset)
    return (
      <Shell
        icon={MapPinOff}
        tone="bg-sky-50 text-sky-700"
        eyebrow="Not in the demo dataset"
        title="This preview only covers the example buildings"
        testid="not-in-demo-search"
      >
        <p>
          Stoop is running in <strong className="text-ink">demo mode</strong>, which replays NYC data recorded in October 2026 for
          five example buildings and makes no live calls. “{report.input_address}” is not one of them, so it was not looked up.
          This says nothing about the building.
        </p>
        <p>To research any NYC address, run Stoop with live NYC Open Data (DATA_MODE=live).</p>
        <ExampleLinks />
      </Shell>
    );
  const unavailable = r.outcome === "source_unavailable";
  return (
    <Shell
      icon={unavailable ? CloudOff : SearchX}
      tone={unavailable ? "bg-rose-50 text-rose-700" : "bg-paper text-ink-soft"}
      eyebrow={unavailable ? "Address service unavailable" : "No reliable match"}
      title={unavailable ? "The city's address service didn't respond" : "We couldn't confidently identify this building"}
      testid={unavailable ? "unavailable" : "no-match"}
    >
      <p>{r.explanation}</p>
      <p>
        {unavailable
          ? "No records were checked, so this says nothing about the building. Please try again in a minute."
          : "Rather than show records that may belong to a different building, no report was produced. Check the house number and street, add the borough or ZIP code, or try one of the examples."}
      </p>
      {r.links.length > 0 && (
        <div className="flex flex-wrap gap-4 text-sm">
          {r.links.map((l) => (
            <ExternalLink key={l.url} href={l.url}>
              {l.label}
            </ExternalLink>
          ))}
        </div>
      )}
      {demo && <ExampleLinks />}
    </Shell>
  );
}
