"""MVP evaluation: deterministic baseline vs the LangGraph workflow, on the same evidence.

Offline (demo fixtures). Writes data/eval/mvp_evaluation.json and prints a table.

Arms
- baseline: the deterministic report alone.
- agent (scripted): the full LangGraph workflow with a scripted model that returns, for
  each address, one faithful paragraph and five known-bad paragraphs (invented number,
  unknown citation, safety verdict, order described as current, another building's
  record presented as this one). No real LLM is called: no API key is configured, and a
  scripted model makes the guardrail measurement reproducible. A real-model run needs
  LLM_ENABLED=1 and a key, and is reported separately when available.

Usage: uv run python scripts/evaluate_reports.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.investigation.agent import investigate
from nyc_apartment_agent.investigation.collect import DEMO_ADDRESSES
from nyc_apartment_agent.investigation.schema import Scope
from nyc_apartment_agent.investigation.verify import verify_report, wording_problems
from nyc_apartment_agent.records.redaction import needs_redaction

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "eval" / "mvp_evaluation.json"

# Approved Phase 1 labels (data/eval/candidate_addresses_draft.csv) and Phase 2 ground truth.
CASES = [
    {
        "id": "west_farms_1134",
        "address": DEMO_ADDRESSES["west_farms_1134"],
        "expect": ("verified", "building", "2027540005", "2006185"),
        "other_building_records": ["dob_complaint:2411142"],
        "this_building_records": ["dob_complaint:2406493"],
    },
    {
        "id": "west_17_220",
        "address": DEMO_ADDRESSES["west_17_220"],
        "expect": ("verified", "building", "1007660055", "1081543"),
    },
    {
        "id": "west_farms_1217",
        "address": DEMO_ADDRESSES["west_farms_1217"],
        "expect": ("verified", "tax_lot", "2029930031", None),
    },
    {
        "id": "fifth_ave_350",
        "address": DEMO_ADDRESSES["fifth_ave_350"],
        "expect": ("verified", "building", "1008350041", "1015862"),
    },
    {
        "id": "acp_1845",
        "address": DEMO_ADDRESSES["acp_1845"],
        "expect": ("verified", "building", "1018227502", "1054974"),
    },
    {
        "id": "ambiguous_100_broadway",
        "address": "100 Broadway",
        "expect": ("ambiguous", None, None, None),
    },
    {
        "id": "wrong_borough_west_farms",
        "address": "1134 West Farms Rd, Brooklyn",
        "expect": ("ambiguous", None, None, None),
    },
    {
        "id": "nonsense",
        "address": "zzqx qqzz nowhere",
        "expect": ("no_reliable_match", None, None, None),
    },
    {
        "id": "outside_nyc",
        "address": "1600 Pennsylvania Avenue NW, Washington, DC 20500",
        "expect": ("no_reliable_match", None, None, None),
    },
]

FREE_TEXT = (
    "Description (unit detail redacted)",
    "Agency resolution (allowlisted text)",
    "DOB recorded address",
)


class ScriptedLLM:
    provider, model = "scripted", "guardrail-suite-v1"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, max_tokens: int) -> str:
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {"focus": [], "tool_calls": [{"tool": "notable_events", "args": {"limit": 3}}]}
            )
        return json.dumps({"paragraphs": self.paragraphs})


def scripted_paragraphs(report) -> list[dict]:
    """One faithful paragraph built from a real finding, plus five known-bad ones."""
    good_f = next((f for f in report.findings if f.fact is not None and f.fact.value > 0), None)
    paras = []
    if good_f is not None:
        n = int(good_f.fact.value)
        paras.append(
            {
                "text": f"The records behind '{good_f.title}' number {n}; ask the "
                "landlord about them and check the agency's site.",
                "finding_ids": [good_f.id],
            }
        )
    any_f = report.findings[0].id
    paras += [
        {"text": "There are 4,321 open violations here.", "finding_ids": [any_f]},
        {"text": "Records confirm a problem.", "evidence_ids": ["hpd_violation:0"]},
        {"text": "Based on this, the apartment is unsafe to rent.", "finding_ids": [any_f]},
        {"text": "The vacate order is currently in effect.", "finding_ids": [any_f]},
    ]
    other = next((e for e in report.evidence if e.attribution == "other_building_on_lot"), None)
    if other is not None:
        paras.append({"text": f"{other.title} is on record here.", "evidence_ids": [other.id]})
    return paras[:5]


def score(case: dict, report) -> dict:
    r = report.resolution
    exp = case["expect"]
    got = (r.outcome, r.level, r.bbl, r.bin)
    idx = report.evidence_index()
    v = verify_report(report)
    wrong_building = 0
    for f in report.findings:
        if f.scope is Scope.THIS_BUILDING:
            wrong_building += sum(
                1
                for i in f.evidence_ids
                if i in idx
                and idx[i].attribution != "this_building"
                and idx[i].source not in ("hpd_buildings", "hpd_registrations")
            )
    for eid in case.get("other_building_records", []):
        if any(eid in f.evidence_ids for f in report.findings if f.scope is Scope.THIS_BUILDING):
            wrong_building += 1
    gt_this_ok = all(
        idx.get(e) and idx[e].attribution == "this_building"
        for e in case.get("this_building_records", [])
    )
    missing_cites = sum(1 for f in report.findings for i in f.evidence_ids if i not in idx)
    incomplete = [
        s
        for s in report.sources
        if s.status in ("incomplete_pagination", "source_unavailable", "error", "not_in_demo")
    ]
    flagged = all(
        any(
            s.dataset_name in f.statement
            or s.dataset_name in f.title
            or (s.status == "not_in_demo" and f.category.value == "unavailable")
            for f in report.findings
        )
        for s in incomplete
    )
    current_order_claims = sum(
        1
        for f in report.findings
        if "historical order presented as current" in wording_problems(f.statement)
    )
    privacy = sum(
        1
        for e in report.evidence
        for k in FREE_TEXT
        if isinstance(e.details.get(k), str) and needs_redaction(e.details[k])
    ) + report.model_dump_json().count('"unique_key"')
    return {
        "identity_correct": got == exp,
        "identity": got,
        "findings": len(report.findings),
        "facts_checked": v.facts_checked,
        "citations_checked": v.citations_checked,
        "verification_passed": v.passed,
        "missing_citations": missing_cites,
        "wrong_building_attributions": wrong_building,
        "ground_truth_this_building_ok": gt_this_ok,
        "incomplete_sources": [s.source for s in incomplete],
        "incomplete_sources_flagged": flagged,
        "orders_presented_as_current": current_order_claims,
        "privacy_violations": privacy,
    }


def main() -> int:
    settings = Settings(_env_file=None, data_mode="demo")
    rows = []
    for case in CASES:
        t0 = time.monotonic()
        base = investigate(case["address"], use_llm=False, settings=settings)
        base_s = time.monotonic() - t0
        llm = ScriptedLLM()
        llm.paragraphs = scripted_paragraphs(base) if base.findings else []
        t0 = time.monotonic()
        agent = investigate(case["address"], llm=llm, settings=settings)
        agent_s = time.monotonic() - t0
        sb, sa = score(case, base), score(case, agent)
        bad_sent = max(0, len(llm.paragraphs) - 1)
        a = agent.agent
        rows.append(
            {
                "id": case["id"],
                "address": case["address"],
                "baseline": {**sb, "seconds": round(base_s, 3)},
                "agent": {
                    **sa,
                    "seconds": round(agent_s, 3),
                    "agent_status": a.status if a else None,
                    "llm_calls": agent.run.llm_calls,
                    "tool_calls": len(a.tool_calls) if a else 0,
                    "paragraphs_offered": len(llm.paragraphs),
                    "known_bad_offered": bad_sent,
                    "paragraphs_kept": len(a.paragraphs) if a else 0,
                    "known_bad_kept": sum(
                        1
                        for p in (a.paragraphs if a else [])
                        if not p.text.startswith("The records behind")
                    ),
                    "findings_identical_to_baseline": [f.statement for f in agent.findings]
                    == [f.statement for f in base.findings],
                },
            }
        )
    summary = {
        "cases": len(rows),
        "identity_correct": sum(r["baseline"]["identity_correct"] for r in rows),
        "wrong_building_attributions": sum(
            r["baseline"]["wrong_building_attributions"] + r["agent"]["wrong_building_attributions"]
            for r in rows
        ),
        "missing_citations": sum(r["baseline"]["missing_citations"] for r in rows),
        "facts_rechecked": sum(r["baseline"]["facts_checked"] for r in rows),
        "citations_rechecked": sum(r["baseline"]["citations_checked"] for r in rows),
        "all_reports_verified": all(
            r["baseline"]["verification_passed"] and r["agent"]["verification_passed"] for r in rows
        ),
        "incomplete_sources_flagged": all(
            r["baseline"]["incomplete_sources_flagged"] for r in rows
        ),
        "orders_presented_as_current": sum(
            r["baseline"]["orders_presented_as_current"] for r in rows
        ),
        "privacy_violations": sum(
            r["baseline"]["privacy_violations"] + r["agent"]["privacy_violations"] for r in rows
        ),
        "agent_known_bad_offered": sum(r["agent"]["known_bad_offered"] for r in rows),
        "agent_known_bad_kept": sum(r["agent"]["known_bad_kept"] for r in rows),
        "agent_faithful_kept": sum(
            r["agent"]["paragraphs_kept"] - r["agent"]["known_bad_kept"] for r in rows
        ),
        "agent_faithful_offered": sum(1 for r in rows if r["agent"]["paragraphs_offered"]),
        "max_llm_calls_per_run": max(r["agent"]["llm_calls"] for r in rows),
        "agent_findings_identical_to_baseline": all(
            r["agent"]["findings_identical_to_baseline"] for r in rows
        ),
        "baseline_seconds_max": max(r["baseline"]["seconds"] for r in rows),
    }
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str) + "\n")
    print(f"{'case':28} {'identity':9} {'wrongBldg':9} {'verified':9} {'agent kept/bad':15}")
    for r in rows:
        b, a = r["baseline"], r["agent"]
        print(
            f"{r['id']:28} {str(b['identity_correct']):9} "
            f"{b['wrong_building_attributions']:<9} {str(b['verification_passed']):9} "
            f"{a['paragraphs_kept']}/{a['known_bad_kept']}"
        )
    print(json.dumps(summary, indent=1))
    ok = (
        summary["identity_correct"] == len(rows)
        and summary["wrong_building_attributions"] == 0
        and summary["all_reports_verified"]
        and summary["agent_known_bad_kept"] == 0
        and summary["privacy_violations"] == 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
