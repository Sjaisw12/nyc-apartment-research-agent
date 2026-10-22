"""D9 on recorded data: before/after BuildingID attribution for the five test addresses.

Replays the violations, complaints and HPD buildings cassettes (recorded 2026-10-21/22).
At 220 W 17th, 87 violation rows and 84 complaint-problem rows have no usable BIN; their
BuildingIDs (805523, 30619) map to 224 and 218 Front buildings on the same lot (BINs
1081547, 1081549), not to the requested building 806076 (BIN 1081543).
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.building_id_attribution import (
    apply_to_complaints,
    apply_to_violations,
)
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import Attribution, AttributionBasis
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
TODAY = dt.date(2026, 10, 22)
NAMES = ("violations", "complaints", "buildings")
META = {n: json.loads((FIXTURES / f"{n}_cassette.meta.json").read_text("utf-8")) for n in NAMES}


@pytest.fixture(scope="module")
def entries():
    merged: dict = {}
    for n in NAMES:
        merged.update(load_cassette(FIXTURES / f"{n}_cassette.json"))
    return merged


def run(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    address = META["buildings"]["addresses"][key]["address"]
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(address, g, s)

        def kw(n):
            return {"page_size": META[n]["page_size"], "digest_key": b"k", "today": TODAY}

        b = retrieve_hpd_buildings(res, s, **kw("buildings"))
        v = retrieve_hpd_violations(res, s, **kw("violations"))
        c = retrieve_hpd_complaints(res, s, **kw("complaints"))
    assert transport.misses == []
    return v, c, apply_to_violations(v, b, as_of=TODAY), apply_to_complaints(c, b, as_of=TODAY)


def test_220_w_17th_rows_move_to_other_buildings_never_to_the_requested_one(entries):
    v, c, v2, c2 = run("west_17_220", entries)
    rv, rc = v2.building_id_attribution, c2.building_id_attribution
    assert rv.before == {
        "other_building_on_lot": 63,
        "this_building": 24,
        "unattributed_on_lot": 87,
    }
    assert rv.after == {"other_building_on_lot": 150, "this_building": 24}
    assert rv.upgraded_by_building_id == {
        "30619": {"mapped_bin": "1081549", "attribution": "other_building_on_lot", "rows": 26},
        "805523": {"mapped_bin": "1081547", "attribution": "other_building_on_lot", "rows": 61},
    }
    assert rc.before == {"other_building_on_lot": 8, "this_building": 13, "unattributed_on_lot": 84}
    assert rc.after == {"other_building_on_lot": 92, "this_building": 13}
    assert rc.upgraded == {"other_building_on_lot": 84}
    assert rc.complaints_before == {
        "other_building_on_lot": 8,
        "this_building": 10,
        "unattributed_on_lot": 61,
    }
    assert rc.complaints_after == {"other_building_on_lot": 69, "this_building": 10}
    # The requested building's counts are unchanged; every moved row says why.
    assert v2.summary.by_attribution[Attribution.THIS_BUILDING].records == 24
    moved = [r for r in v2.records + c2.records if r.original_attribution is not None]
    assert len(moved) == 87 + 84
    assert {r.attribution_basis for r in moved} == {AttributionBasis.HPD_BUILDING_ID}
    assert all(r.attribution is Attribution.OTHER_BUILDING_ON_LOT for r in moved)
    assert v.building_id_attribution is None  # the original results are not modified


def test_1217_west_farms_stays_tax_lot_level(entries):
    _, _, v2, c2 = run("west_farms_1217", entries)
    for rep in (v2.building_id_attribution, c2.building_id_attribution):
        assert not rep.applied and rep.not_applied_reason == "not_a_building_level_join"
    assert {r.attribution for r in v2.records + c2.records} == {Attribution.LOT_LEVEL}


def test_empire_state_conflicting_row_stays_unattributed(entries):
    _, _, v2, c2 = run("fifth_ave_350", entries)
    assert v2.building_id_attribution.not_applied_reason == "source_status_no_records_found"
    rc = c2.building_id_attribution
    assert rc.before == rc.after and rc.kept_unattributed == {"row_lot_not_confirmed": 1}


@pytest.mark.parametrize("key", ["west_farms_1134", "acp_1845"])
def test_already_attributed_lots_are_unchanged(key, entries):
    _, _, v2, c2 = run(key, entries)
    for rep in (v2.building_id_attribution, c2.building_id_attribution):
        assert rep.applied and rep.before == rep.after and rep.upgraded == {}
        assert rep.bin_building_id_disagreements == 0
