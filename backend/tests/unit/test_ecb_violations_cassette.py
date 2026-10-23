"""End to end, offline: real resolver -> HPD buildings -> ECB violations, on recorded data.

Replays three committed recordings, unchanged:
- tests/fixtures/hpd/buildings_cassette.json (2026-10-22): resolver responses and the
  Task 3 HPD building records for the five addresses;
- tests/fixtures/dob311/footprints_cassette.json and dob_ecb_cassette.json
  (2026-10-23 04:51Z, Task 5 step 1). The adapter must build exactly the recorded URLs:
  any miss fails the test.

Expected values are Sanyam's recorded acceptance targets (3, 4, 12, 241 = 147 + 94,
52 with 23 on the condo base lot) and the draft attribution ground truth
(docs/task5/attribution_ground_truth_DRAFT.md, rows 3, 7, 8, 11, 15, 16, 20, 21, 22).
"""

import datetime as dt
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.ecb_models import Adjudication, MatchedBy
from nyc_apartment_agent.records.ecb_violations import retrieve_ecb_violations
from nyc_apartment_agent.records.evidence import InMemoryEvidenceStore, verify
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import (
    Attribution,
    DigestBasis,
    EvidenceKind,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HPD_META = json.loads((FIXTURES / "hpd" / "buildings_cassette.meta.json").read_text("utf-8"))
DOB_META = json.loads((FIXTURES / "dob311" / "cassettes.meta.json").read_text("utf-8"))
DATA_AS_OF = "Thu, 22 Oct 2026 17:04:42 GMT"
TODAY = dt.date(2026, 10, 23)
KEY = b"cassette-test-key"
THIS, OTHER = Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT
LOT = Attribution.LOT_LEVEL


@pytest.fixture(scope="module")
def results():
    hpd_entries = load_cassette(FIXTURES / "hpd" / "buildings_cassette.json")
    dob_entries = {
        **load_cassette(FIXTURES / "dob311" / "footprints_cassette.json"),
        **load_cassette(FIXTURES / "dob311" / "dob_ecb_cassette.json"),
    }
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
            result = retrieve_ecb_violations(
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
    return Counter((r.attribution, r.source_bin, r.source_lot) for r in result.records)


def test_recorded_where_clauses_are_reproduced(results):
    for key, (result, _) in results.items():
        assert result.provenance.where == DOB_META["addresses"][key]["ecb"]["where"], key
        assert result.provenance.order_by == "isn_dob_bis_extract"


def test_1134_west_farms_3(results):
    result, _ = results["west_farms_1134"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 3
    assert counts(result) == {(THIS, "2006185", "0005"): 3}  # ground truth row 3


def test_220_w_17th_4_with_3_on_other_buildings(results):
    result, _ = results["west_17_220"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 4
    assert counts(result) == {  # ground truth rows 7 and 8
        (THIS, "1081543", "0055"): 1,
        (OTHER, "1081547", "0055"): 2,
        (OTHER, "1081548", "0055"): 1,
    }
    fin = result.summary.requested_building_financials
    assert fin.summonses == 1  # other buildings never enter the requested building's money


def test_1217_west_farms_12_lot_level_with_no_bin_clause(results):
    result, _ = results["west_farms_1217"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 12
    assert result.join_scope is JoinScope.TAX_LOT
    assert counts(result) == {(LOT, "2128717", "0031"): 12}  # ground truth row 11
    assert result.bin_clause is None and "bin" not in result.provenance.where
    assert result.bin_evidence == [] and result.resolved_bin is None
    assert all(r.matched_by == [MatchedBy.LOT] for r in result.records)
    assert not result.summary.building_specific_claims_allowed
    assert result.summary.requested_building_financials is None
    assert [p.dataset_id for p in result.bin_evidence_provenance] == ["5zhs-2jue"]


def test_350_5th_241_split_147_five_digit_and_94_four_digit(results):
    result, _ = results["fifth_ave_350"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 241
    assert counts(result) == {  # ground truth rows 15 and 16
        (THIS, "1015862", "00041"): 147,
        (THIS, "1015862", "0041"): 94,
    }
    assert result.summary.lot_format_counts == {"five_digit": 147, "four_digit": 94}
    assert result.lot_formats_queried == ["1008350041:0041", "1008350041:00041"]
    assert len({r.isn_dob_bis_extract for r in result.records}) == 241


def test_1845_acp_52_with_23_on_the_condo_base_lot(results):
    result, _ = results["acp_1845"]
    assert result.status is RetrievalStatus.RECORDS_FOUND and len(result.records) == 52
    assert counts(result) == {  # ground truth rows 20, 21 and 22
        (THIS, "1054974", "7502"): 17,
        (THIS, "1054974", "00001"): 15,
        (THIS, "1054974", "0001"): 8,
        (OTHER, "1055002", "7502"): 12,
    }
    base = [r for r in result.records if r.row_bbl == "1018220001"]
    assert len(base) == 23
    assert all(QualityFlag.CONDO_LOT_FAMILY in r.quality_flags for r in base)
    assert all(r.lot_membership is LotMembership.CONFIRMED for r in base)
    others = [r for r in result.records if r.row_bbl != "1018220001"]
    assert not any(QualityFlag.CONDO_LOT_FAMILY in r.quality_flags for r in others)
    # The other condo building is never reassigned to the requested one.
    assert {r.attribution for r in result.records if r.source_bin == "1055002"} == {OTHER}
    assert result.lot_family == ["1018220001", "1018227502"]


def test_every_recorded_row_is_listed_once_with_verifiable_evidence(results):
    total = 0
    for result, store in results.values():
        assert result.provenance.source_count == len(result.records)
        assert result.provenance.data_as_of == DATA_AS_OF
        for r in result.records:
            total += 1
            raw = store.get(r.original_digest)
            assert raw is not None and verify(raw, KEY)
            assert raw.source_record_id == r.isn_dob_bis_extract
            assert r.digest_basis is DigestBasis.SANITIZED_ROW  # respondents withheld upstream
            assert r.evidence_kind is EvidenceKind.ENFORCEMENT_ACTION
            assert QualityFlag.IDENTIFIER_WITHHELD not in r.quality_flags
            assert QualityFlag.UNCOMMON_VALUE_WITHHELD not in r.quality_flags
    assert total == 3 + 4 + 12 + 241 + 52


def test_recorded_rows_need_no_extra_footprints_lookup(results):
    for result, _ in results.values():
        assert result.row_bins_not_on_lot == []
        assert result.bin_evidence_complete


def test_recorded_statuses_stay_separate_and_use_the_approved_mapping(results):
    every = [r for result, _ in results.values() for r in result.records]
    by_hearing = Counter((r.hearing_status_raw, r.adjudication) for r in every)
    assert by_hearing[("DEFAULT", Adjudication.UPHELD_BY_DEFAULT)] == 4
    assert by_hearing[("WRITTEN OFF", Adjudication.OTHER)] == 4
    assert by_hearing[("DISMISSED", Adjudication.DISMISSED)] == 49
    assert by_hearing[("IN VIOLATION", Adjudication.UPHELD)] == 135
    assert Counter(r.correction_status.value for r in every) == {
        "resolved_with_dob": 307,
        "open_with_dob": 5,
    }
    for r in every:
        if r.hearing_status_raw == "DEFAULT":
            assert "not documented" in r.adjudication_note
    severity = Counter(r.severity.value for r in every)
    assert severity["class_2"] == 78
    for r in every:
        assert (QualityFlag.SEVERITY_UNDOCUMENTED_VALUE in r.quality_flags) is (
            r.severity_raw.startswith("CLASS")
        )


def test_recorded_money_is_decimal_and_only_this_building_is_summed(results):
    result, _ = results["acp_1845"]
    mine = [r for r in result.records if r.attribution is THIS]
    fin = result.summary.requested_building_financials
    assert fin.summonses == 40
    assert fin.balance_due_total == sum((r.balance_due for r in mine), Decimal(0))
    assert all(isinstance(r.penalty_imposed, Decimal) for r in result.records)


def test_no_private_field_reaches_a_public_record(results):
    for result, _ in results.values():
        for r in result.records:
            # The names of withheld fields are listed; no value of theirs is.
            assert {"violation_description", "hearing_time"} <= set(r.withheld_fields)
            blob = r.model_dump_json(exclude={"withheld_fields"})
            for field in ("respondent", "violation_description", "hearing_time", "[WITHHELD]"):
                assert field not in blob
