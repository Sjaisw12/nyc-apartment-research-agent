// Mirrors backend/src/nyc_apartment_agent/investigation/schema.py (report schema v1).

export type Category =
  | "identity"
  | "verified_record"
  | "complaint_allegation"
  | "enforcement_action"
  | "historical_event"
  | "administrative_record"
  | "uncertain"
  | "unavailable";

export type Scope =
  | "this_building"
  | "lot_level"
  | "other_building_on_lot"
  | "unattributed_on_lot"
  | "mixed"
  | "not_applicable";

export interface EvidenceItem {
  id: string;
  source: string;
  agency: string;
  dataset_id: string;
  dataset_name: string;
  record_id: string;
  evidence_kind: string;
  attribution: string;
  title: string;
  date?: string | null;
  date_label?: string | null;
  status?: string | null;
  is_open?: boolean | null;
  tags: string[];
  details: Record<string, string | number | boolean | null>;
  amounts?: Record<string, number>;
  flags: string[];
  source_url?: string | null;
  dataset_url: string;
  data_as_of?: string | null;
}

export interface FactCheck {
  metric: string;
  value: number;
}

export interface Finding {
  id: string;
  section: string;
  category: Category;
  scope: Scope;
  importance: "high" | "medium" | "info";
  title: string;
  statement: string;
  evidence_ids: string[];
  fact?: FactCheck | null;
  caveat?: string | null;
}

export interface Section {
  id: string;
  title: string;
  intro: string;
  finding_ids: string[];
}

export interface TimelineEvent {
  date: string;
  year: number;
  source: string;
  agency: string;
  category: Category;
  attribution: string;
  label: string;
  evidence_id: string;
  notable: boolean;
}

export interface SourceStatus {
  source: string;
  agency: string;
  dataset_id: string;
  dataset_name: string;
  dataset_url: string;
  status: string;
  status_label: string;
  status_detail: string;
  complete: boolean;
  records: number;
  data_as_of?: string | null;
  retrieved_at?: string | null;
  stale?: boolean | null;
  note?: string | null;
}

export interface ResolutionOption {
  bbl: string;
  bin: string | null;
  label: string;
  borough: string;
  bin_evidence: string;
}

export interface OfficialLink {
  label: string;
  url: string;
  note?: string | null;
}

export interface ResolutionView {
  outcome: "verified" | "ambiguous" | "no_reliable_match" | "source_unavailable";
  explanation: string;
  level?: "building" | "tax_lot" | null;
  confidence: "high" | "medium" | "needs_choice" | "none";
  confidence_label: string;
  bbl?: string | null;
  bin?: string | null;
  label?: string | null;
  borough?: string | null;
  bin_evidence?: string | null;
  reasons: string[];
  footprint_bins_on_lot: string[];
  options: ResolutionOption[];
  user_selected: boolean;
  in_demo_dataset: boolean;
  links: OfficialLink[];
}

export interface BuildingOverview {
  hpd_building_id?: string | null;
  legal_stories?: number | null;
  legal_class_a_units?: number | null;
  legal_class_b_units?: number | null;
  dob_building_class?: string | null;
  management_program?: string | null;
  registration_status?: string | null;
  registration_note?: string | null;
  registration_end_date?: string | null;
  buildings_on_lot?: number | null;
  evidence_ids: string[];
}

export interface Stat {
  label: string;
  value: string;
  tone: "neutral" | "attention" | "good";
  finding_id?: string | null;
  hint?: string | null;
}

export interface AgentParagraph {
  text: string;
  evidence_ids: string[];
  finding_ids: string[];
}

export interface AgentAnalysis {
  status: "disabled" | "completed" | "fallback" | "not_applicable";
  status_detail: string;
  provider?: string | null;
  model?: string | null;
  llm_calls: number;
  tool_calls: { tool: string; args: Record<string, unknown> }[];
  paragraphs: AgentParagraph[];
  rejected: { text: string; reasons: string[] }[];
}

export interface RunStep {
  name: string;
  status: string;
  seconds: number;
  detail?: string | null;
}

export interface ResearchReport {
  schema_version: string;
  report_id: string;
  generated_at: string;
  input_address: string;
  disclaimer: string;
  demo_notice?: string | null;
  resolution: ResolutionView;
  building?: BuildingOverview | null;
  headline: string;
  stats: Stat[];
  key_finding_ids: string[];
  sections: Section[];
  findings: Finding[];
  evidence: EvidenceItem[];
  timeline: TimelineEvent[];
  sources: SourceStatus[];
  questions_to_ask: string[];
  limitations: string[];
  agent?: AgentAnalysis | null;
  verification: {
    findings_checked: number;
    facts_checked: number;
    citations_checked: number;
    passed: boolean;
    issues: { finding_id?: string | null; problem: string }[];
  };
  run: {
    mode: "demo" | "live";
    method: string;
    elapsed_seconds: number;
    external_requests: number;
    llm_calls: number;
    steps: RunStep[];
    limits: Record<string, number>;
  };
}

export interface AppConfig {
  data_mode: "demo" | "live";
  llm_available: boolean;
  llm_detail: string;
  examples: { key: string; address: string; why: string }[];
  version: string;
}
