"""End to end, offline: real resolver -> HPD buildings -> DOB complaints, on recorded data.

Replays three committed recordings, unchanged:
- tests/fixtures/hpd/buildings_cassette.json (2026-10-22): resolver responses and the
  Task 3 HPD building records for the five addresses (HPD data last updated 2026-08-12);
- tests/fixtures/dob311/footprints_cassette.json and dob_complaints_cassette.json
  (2026-10-23 04:51Z, Task 5 step 1). The adapter must build exactly the recorded URLs:
  any miss fails the test.

Expected values are the recorded step-1 results and the draft attribution ground truth
(docs/task5/attribution_ground_truth_DRAFT.md, rows 1, 2, 5, 6, 10, 14, 18, 19).
"""

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.dob_complaints import retrieve_dob_complaints
from nyc_apartment_agent.records.dob_models import DobComplaintOutcome
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import Attribution, QualityFlag, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HPD_META = json.loads((FIXTURES / "hpd" / "buildings_cassette.meta.json").read_text("utf-8"))
DOB_META = json.loads((FIXTURES / "dob311" / "cassettes.meta.json").read_text("utf-8"))
DATA_AS_OF = "Thu, 22 Oct 2026 16:12:18 GMT"
TODAY = dt.date(2026, 10, 23)
KEY = b"cassette-test-key"
THIS, OTHER = Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT


@pytest.fixture(scope="module")
def cassettes():
    hpd = load_cassette(FIXTURES / "hpd" / "buildings_cassette.json")
    dob = {
        **load_cassette(FIXTURES / "dob311" / "footprints_cassette.json"),
        **load_cassette(FIXTURES / "dob311" / "dob_complaints_cassette.json"),
    }
    return hpd, dob


@pytest.fixture(scope="module")
def results(cassettes):
    hpd_entries, dob_entries = cassettes
    out = {}
    for key, address in HPD_META["addresses"].items():
        t_hpd, t_dob = ReplayTransport(hpd_entries), ReplayTransport(dob_entries)
        settings = Settings(_env_file=None)
        store = InMemoryEvidenceStore()
        with (
            geosearch_client(settings, t_hpd) as g,
            socrata_client(settings, t_hpd) as s_hpd,
            socrata_client(settings, t_dob) as s_dob,
        ):
            res = resolve_address(address["address"], g, s_hpd)
            hpd = retrieve_hpd_buildings(
                res, s_hpd, page_size=HPD_META["page_size"], digest_key=KEY, today=TODAY
            )
            result = retrieve_dob_complaints(
                res,
                s_dob,
                hpd_buildings=hpd,
                page_size=DOB_META["page_size"],
                digest_key=KEY,
                evidence=store,
                today=TODAY,
            )
        assert t_hpd.misses == [] and t_dob.misses == [], (key, t_dob.misses)
        out[key] = (result, store)
    return out


def counts(result):
    return Counter((r.attribution, r.source_bin) for r in result.records)


def test_1134_west_farms_12_total_with_4_on_a_footprints_only_bin(results):
    result, _ = results["west_farms_1134"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert result.provenance.source_count == 12 and len(result.records) == 12
    assert counts(result) == {(THIS, "2006185"): 8, (OTHER, "2117584"): 4}
    ev = {e.bin: e for e in result.bin_evidence}
    assert [s.value for s in ev["2117584"].sources] == ["footprints"]
    assert QualityFlag.BIN_SINGLE_SOURCE in ev["2117584"].flags
    for r in result.records:
        assert (QualityFlag.BIN_SINGLE_SOURCE in r.quality_flags) is (r.source_bin == "2117584")


def test_220_w_17th_46_total_and_other_buildings_never_count_as_requested(results):
    result, _ = results["west_17_220"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 46
    c = counts(result)
    assert c[(THIS, "1081543")] == 17
    others = {b: n for (a, b), n in c.items() if a is OTHER}
    assert others == {
        "1081547": 19,
        "1081549": 4,
        "1081545": 3,
        "1081544": 1,
        "1081546": 1,
        "1081548": 1,
    }
    assert all(r.source_bin != "1081543" for r in result.records if r.attribution is OTHER)
    s = result.summary.by_attribution
    assert s[THIS].complaints == 17 and s[OTHER].complaints == 29
    assert len(result.search_bins) == 8 and not any(e.flags for e in result.bin_evidence)


def test_1217_west_farms_is_unsupported_with_no_bin_request(results, cassettes):
    result, _ = results["west_farms_1217"]
    assert result.status is RetrievalStatus.UNSUPPORTED_JOIN
    assert result.records == [] and result.search_bins == []
    assert result.provenance.pages == [] and result.bin_evidence_provenance == []


def test_350_5th_141_total(results):
    result, _ = results["fifth_ave_350"]
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert counts(result) == {(THIS, "1015862"): 141}


def test_1845_acp_231_total_split_between_the_two_bins(results):
    result, _ = results["acp_1845"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 231
    assert counts(result) == {(THIS, "1054974"): 111, (OTHER, "1055002"): 120}
    assert result.lot_family == ["1018220001", "1018227502"]


def test_recorded_freshness_and_provenance(results):
    for key in ("west_farms_1134", "west_17_220", "fifth_ave_350", "acp_1845"):
        result, _ = results[key]
        assert result.provenance.data_as_of == DATA_AS_OF
        assert result.bin_evidence_complete
        assert [p.dataset_id for p in result.bin_evidence_provenance] == ["5zhs-2jue", "kj4p-ruqc"]


def test_recorded_outcomes_use_the_approved_mapping(results):
    every = [r for result, _ in results.values() for r in result.records]
    assert len(every) == 12 + 46 + 141 + 231
    outcomes = Counter(r.outcome for r in every)
    assert outcomes[DobComplaintOutcome.NO_VIOLATION] == 241
    assert outcomes[DobComplaintOutcome.NONE] == 3
    unknown = Counter(
        r.disposition_code for r in every if r.outcome is DobComplaintOutcome.UNKNOWN_CODE
    )
    assert unknown == {"N1": 2}  # one of the 19 codes with no official definition
    assert all(r.disposition_description is None for r in every if r.disposition_code == "N1")
    kinds = Counter(r.order_event.kind.value for r in every if r.order_event)
    assert kinds == {
        "stop_work_rescinded_full": 14,
        "stop_work_partial_recorded": 6,
        "vacate_rescinded_full": 1,
        "vacate_partial_issued": 1,
    }


def test_recorded_order_events_never_claim_effect(results):
    for result, _ in results.values():
        if not result.summary:
            continue
        for ev in result.summary.order_events:
            assert ev.still_in_effect == "not_shown_by_source"
            assert "does not show whether the order is still in effect" in ev.statement


def test_recorded_status_disagreements_are_kept(results):
    every = [r for result, _ in results.values() for r in result.records]
    active = [r for r in every if r.complaint_status.value == "active"]
    assert len(active) == 4
    for r in every:
        if QualityFlag.STATUS_FIELDS_DISAGREE in r.quality_flags:
            assert r.status_raw in ("ACTIVE", "CLOSED")


def test_every_record_has_verifiable_evidence(results):
    for result, store in results.values():
        for r in result.records:
            for ref in r.source_rows:  # every copy, stored under its own Socrata :id
                raw = store.get(ref.original_digest)
                assert raw is not None and verify(raw, KEY)
                assert raw.source_record_id == ref.source_row_id
                assert raw.original_row["complaint_number"] == r.complaint_number
            assert "dobrundate" not in r.model_dump_json()


def test_address_aliases_on_the_right_bin_do_not_change_attribution(results):
    # Design test 13: 350 5th rows are recorded under 338 5 AVENUE, WEST 34 STREET, etc.
    result, _ = results["fifth_ave_350"]
    addresses = Counter(r.recorded_address for r in result.records)
    assert addresses["338 5 AVENUE"] == 24 and len(addresses) > 5
    assert {r.attribution for r in result.records} == {THIS}
