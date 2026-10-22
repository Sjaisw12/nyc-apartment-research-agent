"""End to end, offline: resolver -> buildings -> registrations -> registration contacts.

Recorded live 2026-10-22 (scripts/record_hpd_admin_fixtures.py contacts). Person names,
business street addresses and suite numbers were withheld at record time; only the
SHA-256 of each original page body is kept. Roles are what HPD has on file, not proof of
title or of who actually manages a building.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.admin_models import ROLE_NOTE, ContactAssociation
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_contacts import retrieve_hpd_contacts
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.models import Attribution, EvidenceKind, RetrievalStatus
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
META = json.loads((FIXTURES / "contacts_cassette.meta.json").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 10, 22)
PERSON_KEYS = {"firstname", "middleinitial", "lastname", "title"}


@pytest.fixture(scope="module")
def entries():
    return load_cassette(FIXTURES / "contacts_cassette.json")


def run(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(META["addresses"][key]["address"], g, s)
        kw = {"page_size": META["page_size"], "digest_key": b"k", "today": TODAY}
        b = retrieve_hpd_buildings(res, s, **kw)
        r = retrieve_hpd_registrations(res, s, buildings=b, **kw)
        c = retrieve_hpd_contacts(
            res, s, registrations=r, batch_size=META["contact_batch_size"], **kw
        )
    assert transport.misses == []
    return c


def assert_private(result):
    for rec in result.records:
        dumped = rec.model_dump()
        assert not PERSON_KEYS & dumped.keys()
        if rec.names_a_person:
            assert rec.public_city is None and rec.public_zip is None


def test_test_building_contacts_are_corporate_names_and_roles(entries):
    c = run("west_farms_1134", entries)
    assert c.status is RetrievalStatus.RECORDS_FOUND and len(c.records) == 5
    assert all(r.evidence_kind is EvidenceKind.ADMINISTRATIVE_RECORD for r in c.records)
    assert all(r.role_note == ROLE_NOTE for r in c.records)
    (reg,) = c.summary.by_registration
    assert (reg.registration_id, reg.attribution, reg.building_ids_on_lot) == (
        "203625",
        Attribution.THIS_BUILDING,
        ["122027"],
    )
    assert reg.corporate_names_by_role["corporate_owner"] == ["BRONX 1134 WEST FARMS ROAD L.P."]
    assert reg.persons_named_withheld == 3
    # Checked across every HPD registration row with this ID: it covers only this building.
    assert reg.covered_building_ids == ["122027"]
    assert reg.association is ContactAssociation.REQUESTED_BUILDING_ONLY
    assert (c.summary.source_rows, c.summary.total_contacts) == (5, 5)
    # HPD's own description of the registration, reported as that and nothing more.
    assert {r.registration_description for r in c.records} == {"RECEIVERSHIP"}
    assert [x.registration_id for x in c.summary.requested_building] == ["203625"]
    assert_private(c)


def test_eight_registrations_fetched_in_two_batches(entries):
    c = run("west_17_220", entries)
    assert len(c.batch_provenance) == 2 and len(c.records) == 32
    assert c.summary.registrations_requested == c.summary.registrations_checked == 8
    attr = {x.registration_id: x.attribution for x in c.summary.by_registration}
    assert attr.pop("123116") is Attribution.THIS_BUILDING
    assert set(attr.values()) == {Attribution.OTHER_BUILDING_ON_LOT} and len(attr) == 7
    assert [x.registration_id for x in c.summary.requested_building] == ["123116"]
    assert c.summary.duplicate_contact_ids == 0
    # None of the eight registrations covers more than one building (checked citywide).
    assert {x.shared_registration for x in c.summary.by_registration} == {False}
    assert c.summary.coverage_status == "complete"
    assert_private(c)


def test_tax_lot_contacts_stay_lot_level(entries):
    c = run("west_farms_1217", entries)
    assert {r.attribution for r in c.records} == {Attribution.LOT_LEVEL}
    assert c.summary.requested_building is None
    assert not c.summary.building_specific_claims_allowed
    (reg,) = c.summary.by_registration
    assert reg.building_ids_on_lot == ["972462"]  # 1017 HOME STREET: HPD's, not the user's
    assert reg.association is ContactAssociation.LOT_LEVEL
    assert_private(c)


def test_no_registration_means_no_contact_lookup(entries):
    c = run("fifth_ave_350", entries)
    assert c.status is RetrievalStatus.NO_RECORDS_FOUND
    assert c.records == [] and c.batch_provenance == []


def test_condo_two_registrations(entries):
    c = run("acp_1845", entries)
    assert len(c.records) == 10
    attr = {x.registration_id: x.attribution for x in c.summary.by_registration}
    assert attr == {
        "113010": Attribution.THIS_BUILDING,
        "113011": Attribution.OTHER_BUILDING_ON_LOT,
    }
    assert_private(c)


def test_counts_match_the_recording(entries):
    for key, a in META["addresses"].items():
        c = run(key, entries)
        assert c.status.value == a["status"]
        assert len(c.records) == a.get("contacts", 0)
