"""Live checks for Phase 2 Task 3 (HPD administrative records). Skipped unless
RUN_LIVE_TESTS=1. Invariants only: the data changes monthly."""

import os

import pytest

from nyc_apartment_agent.records.admin_models import RequestedBuildingStatus
from nyc_apartment_agent.records.hpd_buildings import DATASET_ID as BUILDINGS
from nyc_apartment_agent.records.hpd_buildings import REQUIRED_FIELDS as BUILDING_FIELDS
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.models import Attribution, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


def run(address, fn=retrieve_hpd_buildings, **kw):
    with geosearch_client() as g, socrata_client() as s:
        res = resolve_address(address, g, s)
        return res, fn(res, s, **kw)


def test_buildings_schema_header_has_every_field_used():
    with socrata_client() as s:
        resp = s.get_json(f"/resource/{BUILDINGS}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in BUILDING_FIELDS + ("bin", "legalclassa", "legalclassb", "registrationid"):
        assert f'"{f}"' in fields


def test_test_building_has_one_current_record_and_freshness():
    _, result = run("1134 West Farms Rd, Bronx, NY 10459")
    assert result.status is RetrievalStatus.RECORDS_FOUND
    rb = result.summary.requested_building
    assert rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD
    assert result.freshness.data_updated_at and result.freshness.metadata_updated_at
    assert result.freshness.stated_update_frequency


def test_tax_lot_buildings_stay_lot_level():
    _, result = run("1217 West Farms Road, Bronx")
    assert {r.attribution for r in result.records} <= {Attribution.LOT_LEVEL}


def test_registrations_schema_and_row_id_paging_is_complete():
    from nyc_apartment_agent.records.hpd_registrations import (
        DATASET_ID,
        REQUIRED_FIELDS,
        retrieve_hpd_registrations,
    )

    with socrata_client() as s:
        resp = s.get_json(f"/resource/{DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in REQUIRED_FIELDS + ("bin", "lastregistrationdate", "registrationenddate"):
        assert f'"{f}"' in fields
    _, result = run("220 West 17th Street, Manhattan", fn=retrieve_hpd_registrations, page_size=3)
    assert result.status is RetrievalStatus.RECORDS_FOUND
    assert len({r.source_row_id for r in result.records}) == len(result.records) >= 8
    assert result.summary.requested_building is not None
    text = result.model_dump_json().lower()
    assert "expired" not in text and "unregistered" not in text


def test_contacts_schema_and_privacy_on_live_data():
    from nyc_apartment_agent.records import hpd_contacts
    from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations

    with socrata_client() as s:
        resp = s.get_json(f"/resource/{hpd_contacts.DATASET_ID}.json", params={"$limit": 1})
    fields = resp.headers["x-soda2-fields"]
    for f in hpd_contacts.REQUIRED_FIELDS + ("corporationname", "firstname", "lastname"):
        assert f'"{f}"' in fields
    with geosearch_client() as g, socrata_client() as s:
        res = resolve_address("1134 West Farms Rd, Bronx, NY 10459", g, s)
        regs = retrieve_hpd_registrations(res, s)
        result = hpd_contacts.retrieve_hpd_contacts(res, s, registrations=regs)
    assert result.status is RetrievalStatus.RECORDS_FOUND and result.records
    for rec in result.records:
        dumped = rec.model_dump()
        assert not {"firstname", "lastname", "middleinitial"} & dumped.keys()
        if rec.names_a_person:
            assert rec.public_city is None and rec.public_zip is None


def test_building_id_attribution_on_live_data_is_conservative():
    import datetime as dt

    from nyc_apartment_agent.records.building_id_attribution import apply_to_violations
    from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
    from nyc_apartment_agent.records.models import AttributionBasis

    with geosearch_client() as g, socrata_client() as s:
        res = resolve_address("220 West 17th Street, Manhattan", g, s)
        b = retrieve_hpd_buildings(res, s)
        v = retrieve_hpd_violations(res, s)
    after = apply_to_violations(v, b, as_of=dt.date.today())
    rep = after.building_id_attribution
    assert rep.applied
    for r in after.records:
        if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID:
            assert r.original_attribution is Attribution.UNATTRIBUTED_ON_LOT and r.mapped_bin
            if r.attribution is Attribution.THIS_BUILDING:
                assert r.mapped_bin == res.building.bin


def test_a_known_shared_registration_covers_buildings_on_several_lots():
    # Measured 2026-10-22: registration 10391 covers 87 HPD buildings across lots.
    from nyc_apartment_agent.records.hpd_contacts import fetch_coverage

    with socrata_client() as s:
        covered, status, _ = fetch_coverage(
            s, ["10391"], batch_size=50, page_size=1000, row_cap=5000
        )
    assert status == "complete" and len(covered["10391"]) > 1
