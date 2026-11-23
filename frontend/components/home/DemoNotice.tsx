"use client";

import { Info } from "lucide-react";
import { useConfig } from "@/lib/useConfig";

/** Tells visitors which addresses will work, based on the backend's actual mode. */
export function DemoNotice({ className = "", dark = false }: { className?: string; dark?: boolean }) {
  const txt = dark ? "text-slate-300" : "text-ink-soft";
  const ico = dark ? "text-accent" : "text-brand";
  const strong = dark ? "text-white" : "text-ink";
  const { cfg, failed } = useConfig();
  if (failed)
    return (
      <p className={`flex items-start gap-2 text-sm ${dark ? "text-amber-200" : "text-caution"} ${className}`} data-testid="api-offline">
        <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        The research service is not responding right now. It may be starting up; reports will load once it is back.
      </p>
    );
  if (!cfg) return null;
  if (cfg.data_mode === "live")
    return (
      <p className={`flex items-start gap-2 text-sm ${txt} ${className}`}>
        <Info className={`mt-0.5 h-4 w-4 shrink-0 ${ico}`} aria-hidden />
        Live NYC Open Data: any NYC address is checked against current city records. Reports take about 10 to 30 seconds.
      </p>
    );
  return (
    <p className={`flex items-start gap-2 text-sm ${txt} ${className}`} data-testid="demo-mode-note">
      <Info className={`mt-0.5 h-4 w-4 shrink-0 ${ico}`} aria-hidden />
      <span>
        <strong className={`font-semibold ${strong}`}>Demo mode.</strong> This preview replays recorded NYC data for
        the five example buildings below. Other addresses are not looked up.
      </span>
    </p>
  );
}
