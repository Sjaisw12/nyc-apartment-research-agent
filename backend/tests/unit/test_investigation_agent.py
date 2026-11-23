"""Phase 3 Step 1: bounded LangGraph workflow, with scripted (fake) LLMs. Offline.

No real model is called: FakeLLM returns fixed JSON so the graph, tools, call limits and
verification can be tested deterministically, including adversarial model output.
"""

import json

import pytest

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.investigation.agent import investigate, run_tool
from nyc_apartment_agent.investigation.collect import DEMO_ADDRESSES
from nyc_apartment_agent.investigation.llm import LLMBudget, LLMError, llm_status
from nyc_apartment_agent.investigation.schema import AgentParagraph
from nyc_apartment_agent.investigation.verify import verify_paragraphs

SETTINGS = Settings(_env_file=None, data_mode="demo")
WF = DEMO_ADDRESSES["west_farms_1134"]


class FakeLLM:
    provider, model = "fake", "scripted"

    def __init__(self, write_reply, plan_reply=None, fail=False):
        self.calls = []
        self.plan_reply = plan_reply
        self.write_reply = write_reply
        self.fail = fail

    def complete(self, system, user, max_tokens):
        self.calls.append(user)
        if self.fail:
            raise LLMError("provider down")
        if len(self.calls) == 1:
            return self.plan_reply or json.dumps(
                {
                    "focus": ["F07", "F08"],
                    "tool_calls": [
                        {"tool": "count_by_year", "args": {"finding_id": "F07"}},
                        {"tool": "evidence_for_finding", "args": {"finding_id": "F08", "limit": 3}},
                        {"tool": "shell", "args": {"cmd": "rm -rf /"}},
                        {"tool": "notable_events", "args": {"limit": 3}},
                        {"tool": "notable_events", "args": {"limit": 3}},
                    ],
                }
            )
        return self.write_reply(self)


def finding_id(report, title):
    return next(f.id for f in report.findings if f.title == title)


@pytest.fixture(scope="module")
def baseline():
    return investigate(WF, use_llm=False, settings=SETTINGS)


def test_graph_runs_all_steps_without_llm(baseline):
    names = [s.name for s in baseline.run.steps]
    assert names == ["resolve", "collect_evidence", "build_baseline", "select_findings"]
    assert baseline.agent.status == "disabled" and baseline.run.llm_calls == 0
    assert baseline.verification.passed
    assert baseline.run.method == "langgraph_agent"


def test_disabled_by_default():
    ok, why = llm_status(Settings(_env_file=None))
    assert not ok and "disabled" in why


def test_good_analysis_is_kept_and_bounded(baseline):
    open_id = finding_id(baseline, "Open HPD violations")
    c_id = finding_id(baseline, "Open immediately hazardous (Class C) violations")
    n_open = int(baseline.finding(open_id).fact.value)
    n_c = int(baseline.finding(c_id).fact.value)

    def write(llm):
        return json.dumps(
            {
                "paragraphs": [
                    {
                        "text": f"HPD's data lists {n_open} open violations here, "
                        f"{n_c} of them Class C. Ask the landlord which have been corrected.",
                        "finding_ids": [open_id, c_id],
                        "evidence_ids": [],
                    },
                ]
            }
        )

    llm = FakeLLM(write)
    r = investigate(WF, llm=llm, settings=SETTINGS)
    assert len(llm.calls) == 2 and r.run.llm_calls == 2
    assert r.agent.status == "completed" and len(r.agent.paragraphs) == 1
    # unknown tool refused, and never more than 4 tool calls
    assert len(r.agent.tool_calls) == 4
    assert [s.name for s in r.run.steps][-4:] == [
        "plan",
        "run_tools",
        "write_analysis",
        "verify_analysis",
    ]
    # Record text reaches the model only inside a <data> block.
    assert "<data>" in llm.calls[1] and "</data>" in llm.calls[1]


def test_hallucinations_and_forbidden_claims_are_rejected(baseline):
    c_id = finding_id(baseline, "Open immediately hazardous (Class C) violations")

    def write(llm):
        return json.dumps(
            {
                "paragraphs": [
                    {"text": "There are 999 open violations.", "finding_ids": [c_id]},
                    {"text": "See record.", "evidence_ids": ["hpd_violation:0000"]},
                    {"text": "The building is unsafe to rent.", "finding_ids": [c_id]},
                    {
                        "text": "The vacate order is currently in effect.",
                        "evidence_ids": ["dob_complaint:2406493"],
                    },
                    {
                        "text": "A partial vacate order was served at this address.",
                        "evidence_ids": ["dob_complaint:2411142"],
                    },
                ]
            }
        )

    r = investigate(WF, llm=FakeLLM(write), settings=SETTINGS)
    assert r.agent.status == "fallback" and r.agent.paragraphs == []
    reasons = [" ".join(x.reasons) for x in r.agent.rejected]
    assert "not supported" in reasons[0]
    assert "unknown" in reasons[1]
    assert "verdict" in reasons[2]
    assert "historical order presented as current" in reasons[3]
    assert "another building" in reasons[4]
    kept, rej = verify_paragraphs([AgentParagraph(text="No citation at all.")], r)
    assert not kept and "no citation" in rej[0].reasons
    # The deterministic report of record is untouched.
    assert [f.statement for f in r.findings] == [f.statement for f in baseline.findings]


def test_provider_failure_falls_back(baseline):
    r = investigate(WF, llm=FakeLLM(lambda _: "", fail=True), settings=SETTINGS)
    assert r.agent.status == "fallback" and r.verification.passed
    assert len(r.findings) == len(baseline.findings)


def test_malformed_json_falls_back():
    r = investigate(WF, llm=FakeLLM(lambda _: "not json at all"), settings=SETTINGS)
    assert r.agent.status == "fallback"


def test_tax_lot_analysis_cannot_claim_this_building():
    def write(llm):
        return json.dumps(
            {"paragraphs": [{"text": "This building has heat complaints.", "finding_ids": ["F06"]}]}
        )

    r = investigate(DEMO_ADDRESSES["west_farms_1217"], llm=FakeLLM(write), settings=SETTINGS)
    assert r.agent.status == "fallback"
    assert "tax-lot" in " ".join(r.agent.rejected[0].reasons)


def test_call_budget_is_enforced():
    b = LLMBudget(max_calls=2, process_cap=100)
    b.take()
    b.take()
    with pytest.raises(LLMError):
        b.take()
    cap = LLMBudget(max_calls=5, process_cap=0)
    with pytest.raises(LLMError):
        cap.take()


def test_no_llm_for_unresolved_address():
    llm = FakeLLM(lambda _: "{}")
    r = investigate("zzqx qqzz nowhere", llm=llm, settings=SETTINGS)
    assert llm.calls == [] and r.agent.status == "not_applicable"


def test_tools_are_bounded(baseline):
    many = [e.id for e in baseline.evidence[:50]]
    out = run_tool(baseline, {"tool": "get_evidence", "args": {"ids": many}})
    assert len(out["result"]["records"]) == 8
    assert run_tool(baseline, {"tool": "nope"})["error"]
