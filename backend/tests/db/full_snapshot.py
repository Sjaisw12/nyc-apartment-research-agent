"""One SYNTHETIC research snapshot holding all five result types, for step 6's audits.

Buildings, registrations, contacts, violations and complaints for one resolved building,
with BuildingID attribution (D9) applied to violations and complaints, saved in one
transaction by the server-side writer. Every private value is a distinct sentinel, so a
scan can say exactly which kind of value leaked and where. Every name is invented.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.building_id_attribution import (
    apply_to_complaints,
    apply_to_violations,
)
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_complaints import retrieve_hpd_complaints
from nyc_apartment_agent.records.hpd_contacts import retrieve_hpd_contacts
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.resolver.models import ParsedInput
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.repository import (
    load_building_result,
    load_complaint_result,
    load_contact_result,
    load_registration_result,
    load_violation_result,
    save_research_snapshot,
)
from tests.hpd_helpers import (
    FakeSocrata,
    building_row,
    complaint_row,
    contact_row,
    person_contact,
    registration_row,
    resolution,
    row,
)

TODAY = dt.date(2026, 10, 22)
# A digest key that is itself a sentinel: it must never be stored or logged.
DIGEST_KEY = "SENTINEL-DIGEST-KEY-5f2b"
DB_PASSWORD = "SENTINEL-DB-PASSWORD-81c"  # noqa: S105 - a synthetic sentinel
SETTINGS = Settings(
    _env_file=None,
    evidence_digest_key=DIGEST_KEY,
    database_url=f"postgresql://writer:{DB_PASSWORD}@db.invalid:5432/nyc",
)
KEY = DIGEST_KEY.encode()

RAW_INPUT = "1 SENTINEL-ADDR STREET APT 7QX, BRONX"
UNIQUE_KEY = "987654321012"  # a synthetic 311 request number

# kind -> values of that kind that may exist only in private raw rows (or nowhere).
SENTINELS = {
    "unit detail": ("SENTINEL-APT-9Z", "SENTINEL-STORY-7", "SENTINEL-APT-C4", "7QX"),
    "contact person": ("SENTINEL-FIRST", "SENTINEL-LAST", "SENTINEL-TITLE", "SENTINEL-PCITY"),
    "business address": ("SENTINEL-BHN", "SENTINEL-BIZ-ST", "SENTINEL-SUITE"),
    "typed address": ("SENTINEL-ADDR",),
    "311 identifier": (UNIQUE_KEY,),
    "credential": (DIGEST_KEY, DB_PASSWORD),
}
ALL_SENTINELS = tuple(s for values in SENTINELS.values() for s in values)
# Source keys that must never appear in a readable JSON document.
PRIVATE_KEYS = {
    "apartment",
    "story",
    "unique_key",
    "complaint_anonymous_flag",
    "firstname",
    "middleinitial",
    "lastname",
    "title",
    "businesshousenumber",
    "businessstreetname",
    "businessapartment",
}
# Where withheld-field names are legitimately listed (a count or list of names, no value).
FIELD_NAME_LISTS = {"withheld_field_counts", "withheld_fields", "fields_masked", "redacted_fields"}

LOADERS = {
    "hpd_buildings": load_building_result,
    "hpd_registrations": load_registration_result,
    "hpd_contacts": load_contact_result,
    "hpd_violations": load_violation_result,
    "hpd_complaints": load_complaint_result,
}


def privacy_resolution():
    """A resolver result whose input, explanation and request URLs carry the typed address."""
    res = resolution()
    parsed = ParsedInput(
        raw=RAW_INPUT,
        house_number="1",
        street="SENTINEL-ADDR STREET",
        street_normalized="SENTINEL-ADDR STREET",
        borough="Bronx",
        zip_code=None,
        state=None,
        notes=["apartment 7QX ignored"],
    )
    return res.model_copy(
        update={
            "input": parsed,
            "explanation": f"matched {RAW_INPUT}",
            "provenance": {"requests": [{"url": "https://geosearch/?text=SENTINEL-ADDR"}]},
        }
    )


BIZ = {
    "businesshousenumber": "SENTINEL-BHN",
    "businessstreetname": "SENTINEL-BIZ-ST",
    "businessapartment": "SENTINEL-SUITE",
}
PERSON = {
    "firstname": "SENTINEL-FIRST",
    "lastname": "SENTINEL-LAST",
    "title": "SENTINEL-TITLE",
    "businesscity": "SENTINEL-PCITY",
}

# Requested building 122027 (BIN 2006185) and another current building on the lot.
BUILDINGS = [building_row(122027), building_row(122028, bin="2006186")]
VIOLATIONS = [
    row(1),
    row(
        2,
        apartment="SENTINEL-APT-9Z",
        story="SENTINEL-STORY-7",
        novdescription="REPAIR LEAK IN APT 7QX AT 3RD STORY",
    ),
    row(3, bin=None, buildingid="122028"),  # D9: moves to the other building
    row(4, bin=None, buildingid="122027"),  # D9: moves to the requested building
    row(5, bin="2006186"),
]
COMPLAINTS = [
    complaint_row(1, cid=100, apartment="SENTINEL-APT-C4", unique_key=UNIQUE_KEY),
    complaint_row(2, cid=100, bin=None, building_id="122028"),
    complaint_row(
        3,
        cid=101,
        complaint_anonymous_flag="Y",
        status_description="THE TENANT IN APT 7QX REPORTED THE CONDITION.",
    ),
    complaint_row(4, cid=102, bin="2006186"),
]
REGISTRATIONS = [registration_row("r-1")]
CONTACTS = [
    contact_row("c-01", **BIZ),  # corporate owner: name and city stay public
    person_contact("c-02", **BIZ, **PERSON),
    person_contact("c-03", type_="Agent", **BIZ, **PERSON),
]


@dataclass
class FullSnapshot:
    res: object
    results: dict  # result type -> source result as saved
    buffer: BufferedEvidenceStore
    run_id: object = None
    saved: dict | None = None


def build() -> FullSnapshot:
    res = privacy_resolution()
    buffer = BufferedEvidenceStore()
    kw = {"evidence": buffer, "digest_key": KEY, "page_size": 10, "today": TODAY}
    fake = FakeSocrata(
        [],
        datasets={
            "kj4p-ruqc": BUILDINGS,
            "wvxf-dwi5": VIOLATIONS,
            "ygpa-z7cr": COMPLAINTS,
            "tesw-yqqr": REGISTRATIONS,
            "feu5-w2e2": CONTACTS,
        },
    )
    with fake.client() as c:
        buildings = retrieve_hpd_buildings(res, c, **kw)
        regs = retrieve_hpd_registrations(res, c, buildings=buildings, **kw)
        contacts = retrieve_hpd_contacts(res, c, registrations=regs, batch_size=50, **kw)
        violations = retrieve_hpd_violations(res, c, **kw)
        complaints = retrieve_hpd_complaints(res, c, **kw)
    results = {
        "hpd_buildings": buildings,
        "hpd_registrations": regs,
        "hpd_contacts": contacts,
        "hpd_violations": apply_to_violations(violations, buildings, as_of=TODAY),
        "hpd_complaints": apply_to_complaints(complaints, buildings, as_of=TODAY),
    }
    return FullSnapshot(res=res, results=results, buffer=buffer)


def save(engine, snap: FullSnapshot) -> FullSnapshot:
    snap.run_id, snap.saved = save_research_snapshot(
        engine, snap.res, RAW_INPUT, list(snap.results.values()), snap.buffer.items(), SETTINGS
    )
    return snap


def load_all(engine, snap: FullSnapshot) -> dict:
    with engine.connect() as conn:
        return {t: LOADERS[t](conn, snap.saved[t].fetch_id) for t in LOADERS}
