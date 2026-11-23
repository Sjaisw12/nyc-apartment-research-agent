"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { investigate, ApiError } from "@/lib/api";
import type { ResearchReport } from "@/lib/types";
import { useConfig } from "@/lib/useConfig";
import SearchBox from "@/components/SearchBox";
import { Report } from "@/components/report/Report";
import { Ambiguous, EmptyState, ErrorState, Loading, NoMatch } from "@/components/report/States";

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; report: ResearchReport };

function InvestigateView() {
  const params = useSearchParams();
  const router = useRouter();
  const { cfg } = useConfig();
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
        setState({
          kind: "error",
          message: e instanceof ApiError ? e.message : "Something went wrong while building the report.",
        });
      });
    return () => ctl.abort();
  }, [address, bbl, bin, attempt]);

  useEffect(() => {
    if (state.kind === "done" && state.report.resolution.label)
      document.title = `${state.report.resolution.label.replace(/, USA$/, "")} · Stoop`;
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
    <div>
      <div className="no-print border-b border-line bg-paper-deep/50">
        <div className="container-site flex flex-col gap-3 py-4 md:flex-row md:items-center md:gap-6">
          <div className="w-full md:max-w-2xl">
            <SearchBox key={address} initial={address} size="sm" />
          </div>
          {cfg && (
            <p className="text-sm text-ink-faint" data-testid="mode-note">
              {cfg.data_mode === "live"
                ? "Live NYC Open Data · any NYC address"
                : "Demo mode · five recorded example buildings only"}
            </p>
          )}
        </div>
      </div>
      {!address && <EmptyState />}
      {address && state.kind === "loading" && <Loading address={address} live={cfg?.data_mode === "live"} />}
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
    <Suspense fallback={<div className="container-site py-12 text-sm text-ink-faint">Loading…</div>}>
      <InvestigateView />
    </Suspense>
  );
}
