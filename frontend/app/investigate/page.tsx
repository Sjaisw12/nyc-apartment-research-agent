"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { investigate, ApiError } from "@/lib/api";
import type { ResearchReport } from "@/lib/types";
import SearchBox from "@/components/SearchBox";
import { Report } from "@/components/report/Report";
import { Ambiguous, ErrorState, Loading, NoMatch } from "@/components/report/States";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; report: ResearchReport };

function InvestigateView() {
  const params = useSearchParams();
  const router = useRouter();
  const address = (params.get("address") ?? "").trim();
  const bbl = params.get("bbl");
  const bin = params.get("bin");
  const [state, setState] = useState<State>({ kind: "idle" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!address) return;
    const ctl = new AbortController();
    setState({ kind: "loading" });
    investigate(address, bbl ? { bbl, bin: bin || null } : null, ctl.signal)
      .then((report) => setState({ kind: "done", report }))
      .catch((e: unknown) => {
        if ((e as Error).name === "AbortError") return;
        setState({ kind: "error", message: e instanceof ApiError ? e.message : "Something went wrong while building the report." });
      });
    return () => ctl.abort();
  }, [address, bbl, bin, attempt]);

  useEffect(() => {
    if (state.kind === "done" && state.report.resolution.label)
      document.title = `${state.report.resolution.label} · Stoop`;
  }, [state]);

  const pick = useCallback(
    (b: string, n: string | null) => {
      const q = new URLSearchParams({ address, bbl: b });
      if (n) q.set("bin", n);
      router.push(`/investigate?${q.toString()}`);
    },
    [address, router],
  );

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <div className="no-print mb-6 max-w-2xl">
        <SearchBox key={address} initial={address} size="sm" />
      </div>
      {!address && <ErrorState message="Enter an NYC street address to start." />}
      {address && state.kind === "loading" && <Loading address={address} />}
      {state.kind === "error" && <ErrorState message={state.message} onRetry={() => setAttempt((a) => a + 1)} />}
      {state.kind === "done" &&
        (state.report.resolution.outcome === "ambiguous" ? (
          <Ambiguous report={state.report} onPick={pick} />
        ) : state.report.resolution.outcome === "verified" ? (
          <Report report={state.report} />
        ) : (
          <NoMatch report={state.report} />
        ))}
    </div>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-6xl px-4 py-10 text-sm text-ink-faint">Loading…</div>}>
      <InvestigateView />
    </Suspense>
  );
}
