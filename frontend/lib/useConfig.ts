"use client";

import { useEffect, useState } from "react";
import { fetchConfig } from "./api";
import type { AppConfig } from "./types";

let cached: Promise<AppConfig | null> | null = null;

/** Backend /config, fetched once per page load. null while loading or if unreachable. */
export function useConfig() {
  const [cfg, setCfg] = useState<AppConfig | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    cached ??= fetchConfig();
    let live = true;
    cached.then((c) => {
      if (!live) return;
      if (c) setCfg(c);
      else setFailed(true);
    });
    return () => {
      live = false;
    };
  }, []);
  return { cfg, failed };
}
