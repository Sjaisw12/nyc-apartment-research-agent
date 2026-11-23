import type { AppConfig, ResearchReport } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);

export const FALLBACK_EXAMPLES: AppConfig["examples"] = [
  {
    key: "west_farms_1134",
    address: "1134 West Farms Rd, Bronx, NY 10459",
    why: "Bronx walk-up with a long HPD history and a second building on the lot",
  },
  {
    key: "acp_1845",
    address: "1845 Adam Clayton Powell Jr Blvd, Manhattan",
    why: "Harlem condominium: records filed under the condo billing lot",
  },
  {
    key: "west_farms_1217",
    address: "1217 West Farms Road, Bronx",
    why: "Only the tax lot can be verified, so building claims are withheld",
  },
];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function fetchConfig(): Promise<AppConfig | null> {
  try {
    const r = await fetch(`${API_BASE}/config`, { cache: "no-store" });
    if (!r.ok) return null;
    return (await r.json()) as AppConfig;
  } catch {
    return null;
  }
}

export async function investigate(
  address: string,
  selected?: { bbl: string; bin: string | null } | null,
  signal?: AbortSignal,
): Promise<ResearchReport> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}/investigate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ address, selected_option: selected ?? null }),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(
      "The research service could not be reached. It may be starting up; try again in a moment.",
      0,
    );
  }
  if (r.status === 429) throw new ApiError("Too many searches in a minute. Please wait and retry.", 429);
  if (r.status === 422) throw new ApiError("Please enter an NYC street address, like “350 5th Avenue, Manhattan”.", 422);
  if (!r.ok) throw new ApiError("The research service had a problem and produced no report.", r.status);
  return (await r.json()) as ResearchReport;
}

export function markdownUrl(reportId: string) {
  return `${API_BASE}/reports/${reportId}.md`;
}

export interface CorrectionInput {
  kind: "wrong_building" | "record_incorrect" | "record_outdated" | "privacy" | "other";
  address: string;
  report_id?: string | null;
  record_id?: string | null;
  details: string;
}

export async function submitCorrection(input: CorrectionInput): Promise<{ reference: string }> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}/corrections`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(input),
    });
  } catch {
    throw new ApiError("The correction service could not be reached. Please try again later.", 0);
  }
  if (r.status === 429) throw new ApiError("Too many requests from this connection. Try again in a minute.", 429);
  if (!r.ok) throw new ApiError("Please check the form: an address and a short description are required.", r.status);
  return (await r.json()) as { reference: string };
}
