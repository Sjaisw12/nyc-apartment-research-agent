"""Task 4 step 5: HPD building records, registrations and registration contacts persisted
as append-only snapshots and loaded back, with row-level source lineage.

Synthetic rows are SYNTHETIC and every name is invented; cassette rows are the recorded,
sanitized fixtures (person names, business streets and suites withheld at record time).
"""

# ruff: noqa: S608  (table names come from constants or the catalog; no input)

import datetime as dt
import json
import logging
import uuid
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.admin_models import (
    ContactAssociation,
    ContactRole,
    ContactSourceResult,
    RegistrationStatus,
)
from nyc_apartment_agent.records.building_id_attribution import apply_to_violations
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.hpd_contacts import retrieve_hpd_contacts
from nyc_apartment_agent.records.hpd_registrations import retrieve_hpd_registrations
from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import (
    Attribution,
    AttributionBasis,
    QualityFlag,
    RetrievalStatus,
)
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import write_raw_rows
from nyc_apartment_agent.storage.repository import (
    _INSERT_COVERAGE,
    _INSERT_FETCH,
    _INSERT_LINK,
    _INSERT_RECORD,
    RecordWriteError,
    _coverage_params,
    _fetch_params,
    _links,
    _record_params,
    commit_checked,
    load_building_result,
    load_contact_result,
    load_registration_result,
    load_violation_result,
    save_research_snapshot,
    save_result,
    save_run,
)
from tests.hpd_helpers import (
    FakeSocrata,
    building_row,
    contact_row,
    person_contact,
    registration_row,
    resolution,
)

pytestmark = pytest.mark.db

KEY = b"synthetic-test-key"
TODAY = dt.date(2026, 10, 22)
SETTINGS = Settings(_env_file=None, evidence_digest_key=KEY.decode())
RAW_INPUT = "1 SENTINEL-ADDR STREET, BRONX"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hpd"
# Invented person and business-address values that must never reach a readable column.
SECRETS = ("ZEBULON", "SYNTHETICSON", "PRESIDENT", "SUITE 9", "SYNTHETIC AVENUE", "BROOKLYN")
PRIVATE_KEYS = {
    "firstname",
    "middleinitial",
    "lastname",
    "title",
    "businesshousenumber",
    "businessstreetname",
    "businessapartment",
    "businesscity",
    "businessstate",
    "businesszip",
}
TABLES = (
    "retrieval_run",
    "source_fetch",
    "fetch_page",
    "normalized_record",
    "record_source_row",
    "registration_coverage",
)
LOADERS = {
    "hpd_buildings": load_building_result,
    "hpd_registrations": load_registration_result,
    "hpd_contacts": load_contact_result,
}


# ---- building synthetic results --------------------------------------------------------


class Pipeline:
    """Buildings -> registrations -> contacts over FakeSocrata, one evidence buffer."""

    def __init__(self, res=None):
        self.res = res or resolution()
        self.buffer = BufferedEvidenceStore()

    def buildings(self, rows=None, fake=None):
        fake = fake or FakeSocrata(rows if rows is not None else [building_row(122027)])
        with fake.client() as c:
            return retrieve_hpd_buildings(
                self.res, c, evidence=self.buffer, digest_key=KEY, page_size=10, today=TODAY
            )

    def registrations(self, rows=None, fake=None, buildings=None, page_size=10):
        fake = fake or FakeSocrata(rows if rows is not None else [registration_row("r-1")])
        with fake.client() as c:
            return retrieve_hpd_registrations(
                self.res,
                c,
                evidence=self.buffer,
                digest_key=KEY,
                page_size=page_size,
                today=TODAY,
                buildings=buildings,
            )

    def contacts(self, rows, regs, fake=None, coverage=None, batch_size=50):
        fake = fake or FakeSocrata(rows)
        if coverage is None:
            coverage = [
                {"registrationid": r.registration_id, "buildingid": r.building_id, ":id": f"v{i}"}
                for i, r in enumerate(regs.records)
            ]
        fake.datasets.setdefault("tesw-yqqr", coverage)
        with fake.client() as c:
            return retrieve_hpd_contacts(
                self.res,
                c,
                registrations=regs,
                evidence=self.buffer,
                digest_key=KEY,
                page_size=10,
                batch_size=batch_size,
                today=TODAY,
            )


def snapshot(db, pipe, *results, raw_input=RAW_INPUT):
    return save_research_snapshot(
        db["writer"], pipe.res, raw_input, list(results), pipe.buffer.items(), SETTINGS
    )


def load(db, result_type, fetch_id):
    with db["app_reader"].connect() as conn:
        return LOADERS[result_type](conn, fetch_id)


def counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        out = {t: conn.execute(text(f"select count(*) from records.{t}")).scalar() for t in TABLES}
        out["raw_row"] = conn.execute(text("select count(*) from evidence.raw_row")).scalar()
    return out


def fetch_row(db, fetch_id):
    with db["owner"].connect() as conn:
        return conn.execute(
            text(
                "select status, rows_fetched, source_count, record_count, source_row_count, "
                "coverage_count from records.source_fetch where id = :f"
            ),
            {"f": fetch_id},
        ).one()


def stored_links(db, fetch_id) -> list[tuple]:
    with db["owner"].connect() as conn:
        return conn.execute(
            text(
                "select r.ordinal, l.position, l.source_row_id, l.original_digest "
                "from records.record_source_row l join records.normalized_record r "
                "on r.id = l.record_id where l.fetch_id = :f order by r.ordinal, l.position"
            ),
            {"f": fetch_id},
        ).all()


def assert_lineage_round_trips(db, result, fetch_id):
    """Every :id in every record's source_rows is a stored link, in order, and back."""
    expected = [
        (i, p, ref.source_row_id, ref.original_digest)
        for i, r in enumerate(result.records)
        for p, ref in enumerate(r.source_rows)
    ]
    assert [tuple(x) for x in stored_links(db, fetch_id)] == expected


# ---- ownership and permissions ---------------------------------------------------------


def test_coverage_table_grants_and_evidence_identity_view(db):
    with db["owner"].connect() as conn:
        owner = conn.execute(
            text("select pg_get_userbyid(relowner) from pg_class where oid = cast(:t as regclass)"),
            {"t": "records.registration_coverage"},
        ).scalar()
        grants = conn.execute(
            text(
                "select grantee, privilege_type from information_schema.role_table_grants "
                "where table_schema = 'records' and table_name = :t"
            ),
            {"t": "registration_coverage"},
        ).all()
        identity = conn.execute(
            text(
                "select grantee, privilege_type from information_schema.role_table_grants "
                "where table_schema = 'records' and table_name = 'evidence_identity'"
            )
        ).all()
    assert owner == "nyc_owner"
    by_role: dict[str, set] = {}
    for grantee, priv in grants:
        by_role.setdefault(grantee, set()).add(priv)
    assert by_role.get("app_reader") == {"SELECT"}
    assert by_role.get("ingest_writer") == {"SELECT", "INSERT"}
    assert "evidence_admin" not in by_role and "evidence_retention" not in by_role
    # Source ids per digest: the writer may check them; readers may not see them (future
    # 311 rows use the request number as their source id).
    assert {(g, p) for g, p in identity if g != "nyc_owner"} == {("ingest_writer", "SELECT")}

    pipe = Pipeline()
    regs = pipe.registrations()
    c = pipe.contacts([contact_row("c-01")], regs)
    _, saved = snapshot(db, pipe, regs, c)
    for role in ("app_reader", "writer"):
        for sql in (
            "update records.registration_coverage set contacts = 0",
            "delete from records.registration_coverage",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                with db[role].begin() as conn:
                    conn.execute(text(sql))
    with pytest.raises(DBAPIError, match="permission denied"):
        with db["app_reader"].begin() as conn:
            conn.execute(text("select * from records.evidence_identity"))
    with pytest.raises(DBAPIError, match="permission denied"):
        with db["evidence_admin"].begin() as conn:
            conn.execute(text("select * from records.registration_coverage"))
    assert load(db, "hpd_contacts", saved["hpd_contacts"].fetch_id).result == c


# ---- synthetic round trips ---------------------------------------------------------------


def test_snapshot_round_trip_counts_source_rows_records_and_links_separately(db):
    pipe = Pipeline()
    b = pipe.buildings([building_row(122027), building_row(122028, bin="2006186")])
    same = {"lastregistrationdate": "2025-10-08T00:00:00.000"}
    regs = pipe.registrations(
        [
            registration_row("r-1", **same),
            registration_row("r-2", **same),  # identical business fields: collapsed
            registration_row("r-3", bid=122028, bin="2006186"),  # same registration, shared
        ],
        buildings=b,
    )
    dup = {"registrationcontactid": "20362501"}
    c = pipe.contacts(
        [
            contact_row("c-01", **dup),
            contact_row("c-02", **dup),  # identical copy under another :id
            contact_row("c-03", **dup),  # and another
            person_contact("c-04"),
            contact_row("c-05", type_="Agent", corporationname="SYNTHETIC AGENT INC"),
        ],
        regs,
    )
    assert (
        regs.status is RetrievalStatus.RECORDS_FOUND and c.status is RetrievalStatus.RECORDS_FOUND
    )
    run_id, saved = snapshot(db, pipe, b, regs, c)

    for result_type, result in (
        ("hpd_buildings", b),
        ("hpd_registrations", regs),
        ("hpd_contacts", c),
    ):
        stored = load(db, result_type, saved[result_type].fetch_id)
        assert stored.result == result, result_type
        assert stored.run_id == run_id
        for r in stored.result.records:
            for ref in getattr(r, "source_rows", None) or [r]:
                assert stored.evidence[ref.original_digest].basis is r.digest_basis

    # Registrations: 3 source rows, 2 normalized records, 3 stored links.
    f = fetch_row(db, saved["hpd_registrations"].fetch_id)
    assert (f.rows_fetched, f.source_count, f.record_count, f.source_row_count) == (3, 3, 2, 3)
    assert [len(r.source_rows) for r in regs.records] == [2, 1]
    assert QualityFlag.DUPLICATE_ID_SAME_CONTENT in regs.records[0].quality_flags
    assert_lineage_round_trips(db, regs, saved["hpd_registrations"].fetch_id)
    # Contacts: 5 source rows, 3 normalized records, 5 stored links, 1 coverage row.
    f = fetch_row(db, saved["hpd_contacts"].fetch_id)
    assert (f.rows_fetched, f.source_count, f.record_count, f.source_row_count) == (5, 5, 3, 5)
    assert f.coverage_count == 1
    assert [r.source_row_id for r in c.records[0].source_rows] == ["c-01", "c-02", "c-03"]
    assert_lineage_round_trips(db, c, saved["hpd_contacts"].fetch_id)
    # Buildings: one source row per record, one plain link each.
    f = fetch_row(db, saved["hpd_buildings"].fetch_id)
    assert (f.rows_fetched, f.record_count, f.source_row_count) == (2, 2, 2)
    # Every source row has its own raw row: 2 buildings + 3 registrations + 5 contacts.
    assert counts(db["owner"])["raw_row"] == 10

    # Typed columns: kinds, evidence kind, attribution basis, lot membership.
    with db["owner"].connect() as conn:
        kinds = conn.execute(
            text(
                "select record_kind, evidence_kind, attribution_basis, "
                "lot_membership is null, location_scope is null, count(*) "
                "from records.normalized_record group by 1, 2, 3, 4, 5 order by 1"
            )
        ).all()
    assert [tuple(k) for k in kinds] == [
        ("contact", "administrative_record", "registration_coverage", True, True, 3),
        ("hpd_building", "administrative_record", "row_identifiers", False, True, 2),
        ("registration", "administrative_record", "row_identifiers", False, True, 2),
    ]


def test_repeated_row_in_an_incomplete_fetch_keeps_both_links(db):
    """The pager returns :id r-1 on two pages: the fetch is incomplete, both copies are
    collapsed into one record listing r-1 twice, and both links are stored (0006 dropped
    the per-fetch digest uniqueness that would have refused this)."""
    pipe = Pipeline()
    rows = [registration_row("r-1"), registration_row("r-2", bid=2, bin="2006186")]
    regs = pipe.registrations([*rows, registration_row("r-1")], page_size=2)
    assert regs.status is RetrievalStatus.INCOMPLETE_PAGINATION
    assert [ref.source_row_id for ref in regs.records[0].source_rows] == ["r-1", "r-1"]
    _, saved = snapshot(db, pipe, regs)
    fid = saved["hpd_registrations"].fetch_id
    assert load(db, "hpd_registrations", fid).result == regs
    assert_lineage_round_trips(db, regs, fid)
    f = fetch_row(db, fid)
    assert (f.rows_fetched, f.record_count, f.source_row_count) == (3, 2, 3)


def _contacts_incomplete_with_records(pipe):
    fake = FakeSocrata([registration_row(f"r-{i:02d}", reg=i, bid=i) for i in range(1, 16)])
    fake.fail_at = {2: 500, 3: 500}
    regs = pipe.registrations(fake=fake)
    c = pipe.contacts([contact_row(f"c-{i:02d}", reg=i) for i in range(1, 11)], regs)
    return regs, c


def _contacts_incomplete_no_rows(pipe):
    fake = FakeSocrata([registration_row(f"r-{i:02d}", reg=i, bid=i) for i in range(1, 4)])
    fake.fail_at = {1: 500, 2: 500}
    regs = pipe.registrations(fake=fake)
    return regs, pipe.contacts([contact_row("c-01")], regs)


def _contacts_unavailable(pipe):
    regs = pipe.registrations()
    fake = FakeSocrata([contact_row("c-01")])
    fake.fail_at = {0: 503, 1: 503}
    return regs, pipe.contacts([], regs, fake=fake)


def _registrations_unavailable(pipe):
    fake = FakeSocrata([registration_row("r-1")])
    fake.fail_at = {0: 503, 1: 503}
    regs = pipe.registrations(fake=fake)
    return regs, pipe.contacts([contact_row("c-01")], regs)


def _no_registrations(pipe):
    regs = pipe.registrations([])
    return regs, pipe.contacts([contact_row("c-01")], regs)


def _coverage_unavailable(pipe):
    regs = pipe.registrations([registration_row("r-1", bid=1)])
    fake = FakeSocrata([contact_row("c-01")])
    fake.fail_at = {2: 503, 3: 503}  # contact batch ok; the coverage lookup fails
    return regs, pipe.contacts([], regs, fake=fake)


@pytest.mark.parametrize(
    ("make", "reg_status", "contact_status"),
    [
        (_contacts_incomplete_with_records, "incomplete_pagination", "incomplete_pagination"),
        (_contacts_incomplete_no_rows, "incomplete_pagination", "incomplete_pagination"),
        (_contacts_unavailable, "records_found", "source_unavailable"),
        (_registrations_unavailable, "source_unavailable", "source_unavailable"),
        (_no_registrations, "no_records_found", "no_records_found"),
        (_coverage_unavailable, "records_found", "records_found"),
    ],
)
def test_every_registration_and_contact_status_round_trips(db, make, reg_status, contact_status):
    pipe = Pipeline()
    regs, c = make(pipe)
    assert (regs.status.value, c.status.value) == (reg_status, contact_status)
    _, saved = snapshot(db, pipe, regs, c)
    for result_type, result in (("hpd_registrations", regs), ("hpd_contacts", c)):
        stored = load(db, result_type, saved[result_type].fetch_id)
        assert stored.result == result, result_type
        if result.status is RetrievalStatus.INCOMPLETE_PAGINATION:
            assert stored.result.partial and stored.result.provenance.incomplete_reasons
    if c.status is RetrievalStatus.INCOMPLETE_PAGINATION and c.records:
        assert c.provenance.incomplete_reasons[0].startswith("registration_list_incomplete")
        # A partial registration list cannot state a requested-building status.
        assert regs.summary.requested_building is None
    if make is _contacts_incomplete_no_rows:
        assert c.summary is None and c.provenance.source_count is None


def test_unsupported_join_and_tax_lot_round_trip(db):
    for outcome in (Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH):
        pipe = Pipeline(resolution(outcome=outcome))
        b, regs = pipe.buildings([]), pipe.registrations([])
        c = pipe.contacts([], regs)
        assert {r.status for r in (b, regs, c)} == {RetrievalStatus.UNSUPPORTED_JOIN}
        _, saved = snapshot(db, pipe, b, regs, c)
        for t, r in (("hpd_buildings", b), ("hpd_registrations", regs), ("hpd_contacts", c)):
            assert load(db, t, saved[t].fetch_id).result == r
    pipe = Pipeline(resolution(bin_=None, level=ResolutionLevel.TAX_LOT))
    b = pipe.buildings()
    regs = pipe.registrations(buildings=b)
    c = pipe.contacts([contact_row("c-01")], regs)
    _, saved = snapshot(db, pipe, b, regs, c)
    for t, r in (("hpd_buildings", b), ("hpd_registrations", regs), ("hpd_contacts", c)):
        stored = load(db, t, saved[t].fetch_id).result
        assert stored == r
        assert {x.attribution for x in stored.records} == {Attribution.LOT_LEVEL}
    assert c.records[0].association is ContactAssociation.LOT_LEVEL
    assert c.summary.requested_building is None
    assert b.summary.requested_building.status.value == "not_applicable_tax_lot"


# ---- shared registrations and cross-lot coverage ----------------------------------------


def _coverage_rows(db, fetch_id):
    with db["app_reader"].connect() as conn:
        return conn.execute(
            text(
                "select registration_id, attribution, association, shared_registration, "
                "building_ids_on_lot, covered_building_ids, covered_buildings_off_lot "
                "from records.registration_coverage where fetch_id = :f order by ordinal"
            ),
            {"f": fetch_id},
        ).all()


def test_shared_registration_semantics_are_stored_and_enforced(db):
    cases = {}
    # Shared with another building on this lot.
    pipe = Pipeline()
    regs = pipe.registrations(
        [registration_row("r-1", bid=1), registration_row("r-2", bid=2, bin="2006186")]
    )
    cases["on_lot"] = (pipe, regs, pipe.contacts([contact_row("c-01")], regs))
    # Shared with a building on another lot (found by the citywide coverage lookup).
    pipe = Pipeline()
    regs = pipe.registrations([registration_row("r-1", bid=1)])
    cov = [
        {"registrationid": "203625", "buildingid": "1", ":id": "v1"},
        {"registrationid": "203625", "buildingid": "999001", ":id": "v2"},
    ]
    cases["off_lot"] = (pipe, regs, pipe.contacts([contact_row("c-01")], regs, coverage=cov))
    # Coverage checked: only the requested building.
    pipe = Pipeline()
    regs = pipe.registrations([registration_row("r-1", bid=1)])
    cases["only"] = (pipe, regs, pipe.contacts([contact_row("c-01")], regs))
    # Coverage lookup failed: sharing unknown.
    pipe = Pipeline()
    cases["unknown"] = (pipe, *_coverage_unavailable(pipe))

    stored = {}
    for name, (pipe, regs, c) in cases.items():
        _, saved = snapshot(db, pipe, regs, c)
        fid = saved["hpd_contacts"].fetch_id
        assert load(db, "hpd_contacts", fid).result == c, name
        assert load(db, "hpd_registrations", saved["hpd_registrations"].fetch_id).result == regs
        stored[name] = [tuple(r) for r in _coverage_rows(db, fid)]
    assert stored == {
        "on_lot": [
            ("203625", "this_building", "shared_including_requested_building", True,
             ["1", "2"], ["1", "2"], 0)
        ],
        "off_lot": [
            ("203625", "this_building", "shared_including_requested_building", True,
             ["1"], ["1", "999001"], 1)
        ],
        "only": [("203625", "this_building", "requested_building_only", False, ["1"], ["1"], 0)],
        "unknown": [
            ("203625", "this_building", "requested_building_sharing_unknown", None,
             ["1"], None, None)
        ],
    }  # fmt: skip
    # Registrations: the on-lot shared registration is flagged and counts both buildings.
    on_lot_regs = cases["on_lot"][1]
    assert all(
        QualityFlag.SHARED_REGISTRATION in r.quality_flags
        and r.buildings_on_lot_with_this_registration == 2
        for r in on_lot_regs.records
    )


# ---- recorded data: all five addresses --------------------------------------------------


@pytest.fixture(scope="module")
def contact_entries():
    return load_cassette(FIXTURES / "contacts_cassette.json")


CONTACT_META = json.loads((FIXTURES / "contacts_cassette.meta.json").read_text("utf-8"))


def _recorded(key, entries):
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    buffer = BufferedEvidenceStore()
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        address = CONTACT_META["addresses"][key]["address"]
        res = resolve_address(address, g, s)
        kw = {"page_size": CONTACT_META["page_size"], "digest_key": KEY, "today": TODAY}
        b = retrieve_hpd_buildings(res, s, evidence=buffer, **kw)
        r = retrieve_hpd_registrations(res, s, buildings=b, evidence=buffer, **kw)
        c = retrieve_hpd_contacts(
            res,
            s,
            registrations=r,
            batch_size=CONTACT_META["contact_batch_size"],
            evidence=buffer,
            **kw,
        )
    assert transport.misses == []
    return res, address, b, r, c, buffer


def test_recorded_admin_results_round_trip_for_all_five_addresses(db, contact_entries):
    keys = list(CONTACT_META["addresses"])
    assert len(keys) == 5
    stats = {}
    for key in keys:
        res, address, b, r, c, buffer = _recorded(key, contact_entries)
        _, saved = save_research_snapshot(
            db["writer"], res, address, [b, r, c], buffer.items(), SETTINGS
        )
        row = {}
        for t, result in (("hpd_buildings", b), ("hpd_registrations", r), ("hpd_contacts", c)):
            fid = saved[t].fetch_id
            stored = load(db, t, fid)
            assert stored.result == result, (key, t)
            if t != "hpd_buildings":
                assert_lineage_round_trips(db, result, fid)
            f = fetch_row(db, fid)
            row[t] = {
                "status": f.status,
                "rows_fetched": f.rows_fetched,
                "source_count": f.source_count,
                "records": f.record_count,
                "links": f.source_row_count,
            }
        row["contacts_coverage"] = [
            (x.registration_id, x.association, x.covered_buildings_off_lot)
            for x in _coverage_rows(db, saved["hpd_contacts"].fetch_id)
        ]
        row["roles"] = dict(sorted(Counter(x.role.value for x in c.records).items()))
        row["persons_named"] = sum(x.names_a_person for x in c.records)
        row["registration_status"] = (
            r.summary.requested_building.status.value
            if r.summary and r.summary.requested_building
            else None
        )
        stats[key] = row
    print("\nrecorded admin snapshots:")
    for key, row in stats.items():
        print(key, json.dumps(row, default=str))
    # Every recorded address reloads, and the stored counts agree with the recording.
    for key, row in stats.items():
        expected = CONTACT_META["addresses"][key]
        assert row["hpd_contacts"]["status"] == expected["status"], key
    # Corporate names stay public; any contact naming a person publishes no address detail.
    with db["app_reader"].connect() as conn:
        exposed = conn.execute(
            text(
                "select count(*) from records.normalized_record where record_kind = 'contact' "
                "and (payload->>'names_a_person')::boolean and (payload->>'public_city' "
                "is not null or payload->>'public_zip' is not null)"
            )
        ).scalar()
        corporate = conn.execute(
            text(
                "select count(*) from records.normalized_record where record_kind = 'contact' "
                "and payload->>'corporation_name' is not null"
            )
        ).scalar()
    assert exposed == 0 and corporate > 0


# ---- rollback with real PostgreSQL failures ---------------------------------------------


def _assert_nothing_stored(db):
    assert counts(db["owner"]) == dict.fromkeys([*TABLES, "raw_row"], 0)


def test_incomplete_registration_fetch_cannot_claim_a_status_and_rolls_back(db, caplog):
    caplog.set_level(logging.DEBUG)
    pipe = Pipeline()
    b = pipe.buildings()
    regs, c = _contacts_incomplete_with_records(pipe)
    complete = pipe.registrations(buildings=b)  # a complete fetch's requested-building status
    claimed = complete.summary.requested_building
    assert claimed.status is RegistrationStatus.ON_FILE_END_DATE_PASSED_AFTER_DATA_UPDATE
    # Valid for Pydantic, refused by SQL: a partial fetch states a building's status.
    bad = regs.model_copy(
        update={"summary": regs.summary.model_copy(update={"requested_building": claimed})}
    )
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)") as info:
        snapshot(db, pipe, b, bad, c)
    assert info.value.__cause__ is None
    _assert_nothing_stored(db)  # buildings, raw rows and the run went with it
    for s in SECRETS:
        assert s not in caplog.text


def test_conflicting_source_identifiers_roll_back(db):
    pipe = Pipeline()
    dup = {"registrationcontactid": "20362501"}
    regs = pipe.registrations()
    c = pipe.contacts([contact_row("c-01", **dup), contact_row("c-02", **dup)], regs)
    first = c.records[0]
    a, b = first.source_rows
    # Each :id paired with the OTHER row's digest. Pydantic accepts it (ids unique, first
    # id is the record's); the database refuses it.
    swapped = [
        a.model_copy(update={"original_digest": b.original_digest}),
        b.model_copy(update={"original_digest": a.original_digest}),
    ]
    bad_record = first.model_copy(
        update={"source_rows": swapped, "original_digest": b.original_digest}
    )
    bad = c.model_copy(update={"records": [bad_record]})
    with pytest.raises(RecordWriteError, match=r"\(23514\)"):
        snapshot(db, pipe, regs, bad)
    _assert_nothing_stored(db)


def test_role_label_must_be_hpds_and_rolls_back(db):
    pipe = Pipeline()
    regs = pipe.registrations()
    c = pipe.contacts([contact_row("c-01", type_="Agent")], regs)
    assert c.records[0].role is ContactRole.AGENT
    # An agent can never be stored as the owner.
    bad = c.model_copy(
        update={"records": [c.records[0].model_copy(update={"role": ContactRole.CORPORATE_OWNER})]}
    )
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)"):
        snapshot(db, pipe, regs, bad)
    _assert_nothing_stored(db)


def test_shared_registration_contradiction_rolls_back(db):
    pipe = Pipeline()
    regs = pipe.registrations(
        [registration_row("r-1", bid=1), registration_row("r-2", bid=2, bin="2006186")]
    )
    c = pipe.contacts([contact_row("c-01")], regs)
    # A contact claiming the shared registration is for the requested building only.
    rec = c.records[0].model_copy(
        update={
            "association": ContactAssociation.REQUESTED_BUILDING_ONLY,
            "registration_shared": False,
        }
    )
    bad = c.model_copy(update={"records": [rec]})
    with pytest.raises(RecordWriteError, match=r"\(23514\)"):
        snapshot(db, pipe, regs, bad)
    _assert_nothing_stored(db)
    # A registration row claiming it covers one building while it is flagged shared.
    r0 = regs.records[0].model_copy(update={"buildings_on_lot_with_this_registration": 1})
    bad_regs = regs.model_copy(update={"records": [r0, *regs.records[1:]]})
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)"):
        snapshot(db, pipe, bad_regs)
    _assert_nothing_stored(db)


def test_incomplete_cross_lot_coverage_cannot_be_stored_as_complete(db):
    pipe = Pipeline()
    regs, c = _coverage_unavailable(pipe)
    # The coverage lookup failed, but the summary says it completed.
    bad = c.model_copy(
        update={"summary": c.summary.model_copy(update={"coverage_status": "complete"})}
    )
    with pytest.raises(RecordWriteError, match=r"\(23514\)"):
        snapshot(db, pipe, regs, bad)
    _assert_nothing_stored(db)
    # Coverage claimed for one building while the lookup failed: the coverage row CHECKs
    # (off-lot count, sharing) refuse it.
    cov = c.summary.by_registration[0].model_copy(update={"covered_building_ids": ["1"]})
    bad = c.model_copy(update={"summary": c.summary.model_copy(update={"by_registration": [cov]})})
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)"):
        snapshot(db, pipe, regs, bad)
    _assert_nothing_stored(db)


# ---- SQL-enforced invariants, each rejected as ingest_writer ----------------------------


@pytest.fixture
def writer_txn(db):
    """A writer connection holding a run, the raw rows and a valid contact result."""
    pipe = Pipeline()
    regs = pipe.registrations(
        [registration_row("r-1", bid=1), registration_row("r-2", bid=2, bin="2006186")]
    )
    dup = {"registrationcontactid": "20362501"}
    c = pipe.contacts(
        [contact_row("c-01", **dup), contact_row("c-02", **dup), person_contact("c-03")], regs
    )
    conn = db["writer"].connect()
    run_id = save_run(conn, pipe.res, RAW_INPUT, digest_key=KEY)
    write_raw_rows(conn, pipe.buffer.items(), digest_key=KEY, retention_days=180)
    conn.commit()
    yield conn, run_id, regs, c
    conn.rollback()
    conn.close()


def _insert_all(conn, run_id, result, *, records=None, coverage=None, links=None):
    """Insert a fetch with its records, coverage and links; return the fetch id."""
    fid = uuid.uuid4()
    p = _fetch_params(fid, run_id, uuid.uuid4().hex * 2, "c" * 64, result)
    p["page_count"] = 0  # pages are not under test here
    conn.execute(_INSERT_FETCH, p)
    covs = (
        coverage
        if coverage is not None
        else [
            _coverage_params(fid, i, x)
            for i, x in enumerate(result.summary.by_registration if result.summary else [])
        ]
    )
    if covs:
        conn.execute(_INSERT_COVERAGE, covs)
    recs = records or [
        _record_params(fid, i, r, result.resolved_bbl) for i, r in enumerate(result.records)
    ]
    conn.execute(_INSERT_RECORD, recs)
    conn.execute(
        _INSERT_LINK,
        links
        or [
            link
            for rec, r in zip(recs, result.records, strict=True)
            for link in _links(fid, rec["id"], r, rec["source_record_id"])
        ],
    )
    return fid


def _commit_rejects(conn, pattern):
    with pytest.raises(DBAPIError, match=pattern):
        conn.commit()
    conn.rollback()


def _insert_rejects(conn, fn, pattern):
    with pytest.raises(DBAPIError, match=pattern):
        fn()
    conn.rollback()


def test_valid_contact_snapshot_commits(writer_txn):
    conn, run_id, _, c = writer_txn
    _insert_all(conn, run_id, c)
    conn.commit()


def test_source_rows_must_equal_the_stored_links(writer_txn):
    conn, run_id, _, c = writer_txn
    recs = None

    def dropped_copy():
        nonlocal recs
        fid = uuid.uuid4()
        conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, "d" * 64, "c" * 64, c))
        conn.execute(
            _INSERT_COVERAGE,
            [_coverage_params(fid, i, x) for i, x in enumerate(c.summary.by_registration)],
        )
        recs = [_record_params(fid, i, r, c.resolved_bbl) for i, r in enumerate(c.records)]
        conn.execute(_INSERT_RECORD, recs)
        links = [
            link
            for rec, r in zip(recs, c.records, strict=True)
            for link in _links(fid, rec["id"], r, rec["source_record_id"])
        ]
        conn.execute(_INSERT_LINK, links[:1] + links[2:])  # copy c-02 silently dropped

    dropped_copy()
    _commit_rejects(conn, "stored counts do not match|source_rows differ from its stored links")


def test_link_must_carry_its_raw_rows_source_id(writer_txn):
    conn, run_id, regs, c = writer_txn
    first = c.records[0]
    # Payload and links agree, but :id c-02 is paired with a stored raw row that belongs
    # to another source row (registration r-1, another dataset).
    a, _b = first.source_rows
    foreign = regs.records[0].original_digest
    refs = [a, _b.model_copy(update={"original_digest": foreign})]
    bad = c.model_copy(
        update={"records": [first.model_copy(update={"source_rows": refs}), *c.records[1:]]}
    )
    _insert_all(conn, run_id, bad)
    _commit_rejects(conn, "link's source id is not its raw row's")


@pytest.mark.parametrize(
    ("field", "value", "pattern"),
    [
        ("public_city", "BROOKLYN", "contact_person_has_no_address"),
        ("role", "corporate_owner", "contact_role_is_hpd_type"),
        ("association", "requested_building_only", "contact_association_follows_coverage"),
        ("source_row_id", "c-99", "admin_record_shape|row_lineage_shape"),
        ("firstname", "ZEBULON", "payload_has_no_private_keys"),
        ("businessstreetname", "SYNTHETIC AVENUE", "payload_has_no_private_keys"),
        ("resolved_bbl", "2027540005", "admin_record_shape"),
    ],
)
def test_contact_record_checks(writer_txn, field, value, pattern):
    conn, run_id, _, c = writer_txn
    person = next(r for r in c.records if r.names_a_person)
    fid = uuid.uuid4()
    conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, "e" * 64, "c" * 64, c))
    p = _record_params(fid, 0, person, c.resolved_bbl)
    p["payload"] = {**p["payload"], field: value}
    _insert_rejects(conn, lambda: conn.execute(_INSERT_RECORD, p), pattern)


def test_coverage_row_checks(writer_txn):
    conn, run_id, _, c = writer_txn
    cov = c.summary.by_registration[0]
    for update, pattern in (
        ({"association": ContactAssociation.REQUESTED_BUILDING_ONLY}, "association_follows"),
        ({"shared_registration": False}, "shared_follows_coverage|association_follows"),
        ({"covered_building_ids": ["1"]}, "coverage_includes_lot|shared_follows"),
        ({"covered_buildings_off_lot": 5}, "off_lot_count_follows_coverage"),
    ):
        fid = uuid.uuid4()
        conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, uuid.uuid4().hex * 2, "c" * 64, c))
        p = _coverage_params(fid, 0, cov.model_copy(update=update))
        _insert_rejects(conn, lambda p=p: conn.execute(_INSERT_COVERAGE, p), pattern)


def test_coverage_belongs_only_to_contacts_and_kinds_match(writer_txn):
    conn, run_id, regs, c = writer_txn
    fid = uuid.uuid4()
    conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, "f" * 64, "c" * 64, regs))
    p = _coverage_params(fid, 0, c.summary.by_registration[0])
    _insert_rejects(conn, lambda: conn.execute(_INSERT_COVERAGE, p), "only to a contacts fetch")
    # A contact record under a registrations fetch, and a registration under contacts.
    fid = uuid.uuid4()
    conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, "f" * 64, "c" * 64, regs))
    p = _record_params(fid, 0, c.records[0], regs.resolved_bbl)
    p["dataset_id"] = regs.dataset_id
    p["payload"] = {**p["payload"], "dataset_id": regs.dataset_id}
    _insert_rejects(conn, lambda: conn.execute(_INSERT_RECORD, p), "record kind does not match")


def test_contact_coverage_must_agree_at_commit(writer_txn):
    conn, run_id, _, c = writer_txn
    fid = uuid.uuid4()
    conn.execute(_INSERT_FETCH, _fetch_params(fid, run_id, "a1" * 32, "c" * 64, c))
    recs = [_record_params(fid, i, r, c.resolved_bbl) for i, r in enumerate(c.records)]
    conn.execute(_INSERT_RECORD, recs)
    conn.execute(
        _INSERT_LINK,
        [
            link
            for rec, r in zip(recs, c.records, strict=True)
            for link in _links(fid, rec["id"], r, rec["source_record_id"])
        ],
    )
    # No coverage rows although the fetch declares one.
    _commit_rejects(conn, "stored coverage count does not match")


def test_building_record_checks(db):
    pipe = Pipeline()
    b = pipe.buildings([building_row(122027)])
    with db["writer"].connect() as conn:
        run_id = save_run(conn, pipe.res, RAW_INPUT, digest_key=KEY)
        write_raw_rows(conn, pipe.buffer.items(), digest_key=KEY, retention_days=180)
        conn.commit()
        for update, pattern in (
            ({"is_current": False}, "building_current_follows_lifecycle"),
            ({"building_id": "999"}, "admin_record_shape"),
            ({"evidence_kind": "official_finding"}, "admin_record_shape|payload_matches"),
        ):
            fid = uuid.uuid4()
            conn.execute(
                _INSERT_FETCH, _fetch_params(fid, run_id, uuid.uuid4().hex * 2, "c" * 64, b)
            )
            p = _record_params(fid, 0, b.records[0])
            p["payload"] = {**p["payload"], **update}
            if "evidence_kind" in update:
                p["evidence_kind"] = update["evidence_kind"]
            _insert_rejects(conn, lambda p=p: conn.execute(_INSERT_RECORD, p), pattern)


def test_registration_status_labels_are_only_the_approved_ones(db):
    pipe = Pipeline()
    b = pipe.buildings()
    regs = pipe.registrations(buildings=b)
    with db["writer"].connect() as conn:
        run_id = save_run(conn, pipe.res, RAW_INPUT, digest_key=KEY)
        p = _fetch_params(uuid.uuid4(), run_id, "b" * 64, "c" * 64, regs)
        p["result_payload"]["summary"]["requested_building"]["status"] = "expired"
        _insert_rejects(conn, lambda: conn.execute(_INSERT_FETCH, p), "registration_status_labels")


# ---- BuildingID attribution (D9) basis ---------------------------------------------------


@pytest.fixture(scope="module")
def d9_entries():
    merged: dict = {}
    for n in ("violations", "buildings"):
        merged.update(load_cassette(FIXTURES / f"{n}_cassette.json"))
    return merged


def _d9(entries):
    vmeta = json.loads((FIXTURES / "violations_cassette.meta.json").read_text("utf-8"))
    bmeta = json.loads((FIXTURES / "buildings_cassette.meta.json").read_text("utf-8"))
    transport = ReplayTransport(entries)
    settings = Settings(_env_file=None)
    buffer = BufferedEvidenceStore()
    address = vmeta["addresses"]["west_17_220"]["address"]
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        res = resolve_address(address, g, s)
        v = retrieve_hpd_violations(
            res, s, page_size=vmeta["page_size"], evidence=buffer, digest_key=KEY, today=TODAY
        )
        b = retrieve_hpd_buildings(
            res, s, page_size=bmeta["page_size"], evidence=buffer, digest_key=KEY, today=TODAY
        )
    return res, address, v, b, apply_to_violations(v, b, as_of=TODAY), buffer


def test_d9_result_keeps_its_building_records_and_original_attribution(db, d9_entries):
    res, address, before, b, after, buffer = _d9(d9_entries)
    assert after.building_id_attribution.applied
    _, saved = save_research_snapshot(
        db["writer"], res, address, [b, after], buffer.items(), SETTINGS
    )
    with db["app_reader"].connect() as conn:
        stored = load_violation_result(conn, saved["hpd_violations"].fetch_id)
        again = load_building_result(conn, stored.buildings_fetch_id).result
    assert stored.result == after and again == b
    assert stored.buildings_fetch_id == saved["hpd_buildings"].fetch_id
    report = stored.result.building_id_attribution
    assert report.before == after.building_id_attribution.before
    assert report.upgraded == {"other_building_on_lot": 87}
    moved = [
        r for r in stored.result.records if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID
    ]
    assert len(moved) == 87
    assert {r.original_attribution for r in moved} == {Attribution.UNATTRIBUTED_ON_LOT}
    # Every mapped BIN is the BIN of exactly one stored, current building record.
    by_id = {x.building_id: x for x in b.records}
    for r in moved:
        assert by_id[r.building_id].source_bin == r.mapped_bin and by_id[r.building_id].is_current


def test_d9_without_its_building_records_is_refused(db, d9_entries):
    res, address, _, _, after, buffer = _d9(d9_entries)
    with pytest.raises(RecordWriteError, match=r"CheckViolation \(23514\)"):
        save_research_snapshot(db["writer"], res, address, [after], buffer.items(), SETTINGS)
    _assert_nothing_stored(db)


def test_d9_report_must_describe_the_linked_buildings_and_records(db, d9_entries):
    res, address, before, b, after, buffer = _d9(d9_entries)
    with db["writer"].connect() as conn:
        run_id = save_run(conn, res, address, digest_key=KEY)
        kw = {"digest_key": KEY, "retention_days": 180}
        bs = save_result(conn, run_id, b, buffer.items(), **kw)
        commit_checked(conn)
        # A report naming another data version than the linked building records.
        rep = after.building_id_attribution.model_copy(
            update={"buildings_data_as_of": "2020-01-01T00:00:00.000"}
        )
        stale = after.model_copy(update={"building_id_attribution": rep})
        with pytest.raises(RecordWriteError, match=r"\(23514\)"):
            save_result(conn, run_id, stale, buffer.items(), buildings_fetch_id=bs.fetch_id, **kw)
        conn.rollback()
        # Report counts that disagree with the stored records (one upgrade too many).
        rep = after.building_id_attribution.model_copy(
            update={"upgraded": {"other_building_on_lot": 88}}
        )
        wrong = after.model_copy(update={"building_id_attribution": rep})
        save_result(conn, run_id, wrong, buffer.items(), buildings_fetch_id=bs.fetch_id, **kw)
        with pytest.raises(RecordWriteError, match=r"\(23514\)"):
            commit_checked(conn)
        conn.rollback()
        # A record upgraded through a building ID the linked records do not have.
        i, moved = next(
            (i, r)
            for i, r in enumerate(after.records)
            if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID
        )
        records = list(after.records)
        records[i] = moved.model_copy(update={"building_id": "999999999"})
        forged = after.model_copy(update={"records": records})
        with pytest.raises(RecordWriteError, match=r"\(23514\)"):
            save_result(conn, run_id, forged, buffer.items(), buildings_fetch_id=bs.fetch_id, **kw)
        conn.rollback()
    # A building fetch from another run cannot be the basis.
    with db["writer"].connect() as conn:
        other_run = save_run(conn, res, address, digest_key=KEY)
        with pytest.raises(RecordWriteError, match=r"ForeignKeyViolation"):
            save_result(
                conn, other_run, after, buffer.items(), buildings_fetch_id=bs.fetch_id, **kw
            )
        conn.rollback()


# ---- idempotency --------------------------------------------------------------------------


def test_admin_resave_is_idempotent_and_raw_rows_are_shared(db):
    pipe = Pipeline()
    regs = pipe.registrations([registration_row("r-1"), registration_row("r-2")])
    c = pipe.contacts([contact_row("c-01")], regs)
    with db["writer"].connect() as conn:
        run_id = save_run(conn, pipe.res, RAW_INPUT, digest_key=KEY)
        kw = {"digest_key": KEY, "retention_days": 180}
        first = save_result(conn, run_id, regs, pipe.buffer.items(), **kw)
        commit_checked(conn)
        again = save_result(conn, run_id, regs, pipe.buffer.items(), **kw)
        commit_checked(conn)
    assert first.created and not again.created and again.fetch_id == first.fetch_id
    # A second run over the same rows: a new snapshot, raw rows stored once.
    snapshot(db, pipe, regs, c)
    n = counts(db["owner"])
    assert n["source_fetch"] == 3 and n["raw_row"] == 3
    assert n["record_source_row"] == 2 + 2 + 1


# ---- privacy -------------------------------------------------------------------------------


def _private_keys(doc, path=()):
    found = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in PRIVATE_KEYS and path[-2:] != ("summary", "withheld_field_counts"):
                found.append(".".join((*path, k)))
            found += _private_keys(v, (*path, k))
    elif isinstance(doc, list):
        for v in doc:
            found += _private_keys(v, path)
    return found


def test_no_person_name_or_business_address_reaches_a_readable_column(db, caplog):
    caplog.set_level(logging.DEBUG)
    pipe = Pipeline()
    regs = pipe.registrations()
    c = pipe.contacts(
        [contact_row("c-01"), person_contact("c-02"), person_contact("c-03", type_="Agent")],
        regs,
    )
    _, saved = snapshot(db, pipe, regs, c)
    stored = load(db, "hpd_contacts", saved["hpd_contacts"].fetch_id).result
    assert stored == c
    scanned = 0
    with db["app_reader"].connect() as conn:
        relations = conn.execute(
            text(
                "select table_schema, table_name from information_schema.tables "
                "where table_schema in ('records', 'public') order by 1, 2"
            )
        ).all()
        assert {r.table_name for r in relations} >= {*TABLES, "evidence_availability"}
        assert "evidence_identity" not in {r.table_name for r in relations}
        for schema, name in relations:
            for (value,) in conn.execute(text(f'select t::text from "{schema}"."{name}" t')):
                scanned += 1
                for s in SECRETS:
                    assert s not in value, (schema, name, s)
            for (doc,) in conn.execute(text(f'select to_jsonb(t) from "{schema}"."{name}" t')):
                assert _private_keys(doc) == [], (schema, name)
    assert scanned > 10
    # Corporate names, roles and HPD's raw type labels stay public.
    assert {(r.role, r.role_raw) for r in stored.records} == {
        (ContactRole.CORPORATE_OWNER, "CorporateOwner"),
        (ContactRole.HEAD_OFFICER, "HeadOfficer"),
        (ContactRole.AGENT, "Agent"),
    }
    assert stored.records[0].corporation_name == "SYNTHETIC OWNER LLC"
    # The withheld originals exist only in the private raw rows.
    with db["evidence_admin"].connect() as conn:
        held = conn.execute(
            text("select count(*) from evidence.raw_row where original_row->>'firstname' = :n"),
            {"n": "ZEBULON"},
        ).scalar()
    assert held == 2
    for s in SECRETS:
        assert s not in caplog.text


# ---- step 5 correction: repeated building IDs in registration coverage ------------------


@pytest.fixture
def ingest_txn(db):
    """A connection as a login holding ONLY ingest_writer, with a single-building
    registration whose citywide coverage was checked: requested building only."""
    pipe = Pipeline()
    regs = pipe.registrations([registration_row("r-1", bid=1)])
    c = pipe.contacts([contact_row("c-01"), person_contact("c-02")], regs)
    (cov,) = c.summary.by_registration
    assert (cov.building_ids_on_lot, cov.covered_building_ids) == (["1"], ["1"])
    assert cov.association is ContactAssociation.REQUESTED_BUILDING_ONLY
    conn = db["ingest_writer"].connect()
    run_id = save_run(conn, pipe.res, RAW_INPUT, digest_key=KEY)
    write_raw_rows(conn, pipe.buffer.items(), digest_key=KEY, retention_days=180)
    conn.commit()
    yield conn, run_id, c
    conn.rollback()
    conn.close()


def _forged(c, coverage_status=None, **cov_update):
    """The contacts result with its one coverage row changed, and every place that must
    agree with it (contact records, requested_building) changed to match. model_copy
    does not validate, so this reaches the database even after the Pydantic check."""
    cov = c.summary.by_registration[0].model_copy(update=cov_update)
    records = [
        r.model_copy(
            update={"association": cov.association, "registration_shared": cov.shared_registration}
        )
        for r in c.records
    ]
    summary = c.summary.model_copy(
        update={
            "by_registration": [cov],
            "requested_building": [cov],
            "coverage_status": coverage_status or c.summary.coverage_status,
        }
    )
    return c.model_copy(update={"records": records, "summary": summary})


SHARED = ContactAssociation.SHARED_INCLUDING_REQUESTED_BUILDING
REPEATED_ID_CASES = {
    # One distinct building listed twice citywide: fabricated sharing and off-lot building.
    "covered_repeated": {
        "covered_building_ids": ["1", "1"],
        "shared_registration": True,
        "covered_buildings_off_lot": 1,
        "association": SHARED,
    },
    # One building listed twice on the lot: fabricated on-lot sharing.
    "on_lot_repeated": {
        "building_ids_on_lot": ["1", "1"],
        "covered_building_ids": ["1", "1"],
        "shared_registration": True,
        "covered_buildings_off_lot": 0,
        "association": SHARED,
    },
    # Coverage unknown, but a repeated on-lot ID fabricates sharing anyway.
    "unknown_with_repeat": {
        "building_ids_on_lot": ["1", "1"],
        "covered_building_ids": None,
        "shared_registration": True,
        "covered_buildings_off_lot": None,
        "association": SHARED,
        "coverage_status": "unavailable",
    },
    # Repeated IDs presented as requested-building-only.
    "exclusive_with_repeat": {
        "covered_building_ids": ["1", "1"],
        "shared_registration": False,
        "covered_buildings_off_lot": 1,
        "association": ContactAssociation.REQUESTED_BUILDING_ONLY,
    },
}


@pytest.mark.parametrize("case", list(REPEATED_ID_CASES))
def test_repeated_building_ids_cannot_fabricate_coverage(ingest_txn, case):
    conn, run_id, c = ingest_txn
    forged = _forged(c, **REPEATED_ID_CASES[case])
    # Before 0008 the first three COMMITTED as ingest_writer; the last was already
    # refused by shared_follows_coverage. 0008's CHECK now refuses all four first.
    with pytest.raises(DBAPIError, match="building_ids_distinct"):
        _insert_all(conn, run_id, forged)
        conn.commit()
    conn.rollback()


@pytest.mark.parametrize("case", list(REPEATED_ID_CASES))
def test_pydantic_refuses_repeated_building_ids_before_persistence(case):
    pipe = Pipeline()
    regs = pipe.registrations([registration_row("r-1", bid=1)])
    c = pipe.contacts([contact_row("c-01")], regs)
    data = _forged(c, **REPEATED_ID_CASES[case]).model_dump(mode="json")
    with pytest.raises(ValidationError, match="repeats a building ID"):
        ContactSourceResult.model_validate(data)
    # save_result re-validates before touching the connection, so it never reaches SQL.
    with pytest.raises(ValidationError, match="repeats a building ID"):
        save_result(
            None,
            uuid.uuid4(),
            _forged(c, **REPEATED_ID_CASES[case]),
            [],
            digest_key=KEY,
            retention_days=180,
        )


def test_distinct_coverage_cases_still_store(db):
    """Genuine on-lot sharing, cross-lot sharing, requested-only and unknown are unchanged
    (the full table is checked in test_shared_registration_semantics_are_stored_and_enforced);
    here the empty-but-checked and NULL-unknown distinctions are stored as such."""
    pipe = Pipeline()
    regs, c = _coverage_unavailable(pipe)
    _, saved = snapshot(db, pipe, regs, c)
    (row,) = _coverage_rows(db, saved["hpd_contacts"].fetch_id)
    assert row.covered_building_ids is None and row.shared_registration is None
    assert row.association == "requested_building_sharing_unknown"
