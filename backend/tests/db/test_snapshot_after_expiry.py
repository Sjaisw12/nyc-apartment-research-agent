"""Task 4 step 6: historical snapshots after their raw evidence has expired.

Expiry removes only the private originals. Every stored snapshot must still load as
validated records with its lineage, digests, D9 link and report, registration coverage
and complaint groups, report its evidence as not held, and never offer a tombstone as
verifiable original evidence. The retention job must not remove or rewrite anything in
the records schema.
"""

# ruff: noqa: S608  (table names come from the catalog; no input)

import pytest
from sqlalchemy import text

from nyc_apartment_agent.cassette import load_cassette
from nyc_apartment_agent.records.models import AttributionBasis
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader
from nyc_apartment_agent.storage.repository import (
    load_building_result,
    load_complaint_result,
    load_violation_result,
    save_research_snapshot,
)
from nyc_apartment_agent.storage.retention import COMPLETE, run_retention
from tests.db import full_snapshot as fs
from tests.db.test_admin_repository import FIXTURES, _d9
from tests.db.test_admin_repository import SETTINGS as D9_SETTINGS
from tests.db.test_complaint_repository import _cassette_result as _complaints_d9
from tests.db.test_evidence import age_raw_rows

pytestmark = pytest.mark.db


def records_fingerprint(db) -> dict[str, tuple[int, str]]:
    """Row count and an order-independent hash of every relation in the records schema."""
    with db["owner"].connect() as conn:
        tables = conn.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'records' and table_type = 'BASE TABLE'"
            )
        ).scalars()
        return {
            t: tuple(
                conn.execute(
                    text(
                        f"select count(*), coalesce(md5(string_agg(to_jsonb(x)::text, '|' "
                        f"order by to_jsonb(x)::text)), '') from records.{t} x"
                    )
                ).one()
            )
            for t in list(tables)
        }


def cited_digests(record) -> list[str]:
    rows = getattr(record, "source_rows", None)
    return [ref.original_digest for ref in rows] if rows else [record.original_digest]


def expire_everything(db) -> int:
    age_raw_rows(db, 181)
    report = run_retention(db["evidence_retention"])
    assert report.status == COMPLETE
    return report


def test_all_five_result_types_survive_expiry_intact(db):
    snap = fs.save(db["writer"], fs.build())
    before = fs.load_all(db["app_reader"], snap)
    fingerprint = records_fingerprint(db)
    digests = {t: set(s.evidence) for t, s in before.items()}
    assert all(e.held for s in before.values() for e in s.evidence.values())

    report = expire_everything(db)
    assert report.expired == len(snap.buffer.items())

    # Nothing in the records schema was removed or rewritten.
    assert records_fingerprint(db) == fingerprint
    after = fs.load_all(db["app_reader"], snap)
    for t, stored in after.items():
        # Loaded through the Pydantic models and equal to what was saved.
        assert stored.result == snap.results[t] == before[t].result, t
        assert stored.fetch_id == before[t].fetch_id and stored.run_id == snap.run_id
        # Same digests, every one now reported as not held, with the job's cutoff.
        assert set(stored.evidence) == digests[t]
        assert all(
            not e.held and e.expired_at == report.cutoff for e in stored.evidence.values()
        ), t
        cited = {d for r in stored.result.records for d in cited_digests(r)}
        assert cited == digests[t]

    # D9: the violations and complaints still name the building records they used.
    buildings = after["hpd_buildings"]
    for t in ("hpd_violations", "hpd_complaints"):
        assert after[t].buildings_fetch_id == buildings.fetch_id
        assert after[t].result.building_id_attribution.applied
        by_id = {b.building_id: b for b in buildings.result.records}
        for r in after[t].result.records:
            if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID:
                assert by_id[r.building_id].source_bin == r.mapped_bin
    # Coverage and complaint groups are unchanged and consistent with their records.
    contacts = after["hpd_contacts"].result
    coverage = contacts.summary.by_registration
    assert coverage and coverage == snap.results["hpd_contacts"].summary.by_registration
    assert {c.registration_id for c in coverage} == {r.registration_id for r in contacts.records}
    complaints = after["hpd_complaints"].result
    assert complaints.complaints == snap.results["hpd_complaints"].complaints
    assert {c.complaint_id for c in complaints.complaints} == {
        r.complaint_id for r in complaints.records
    }

    # A tombstone is never offered as verifiable original evidence.
    reader = PostgresEvidenceReader(db["evidence_admin"], fs.KEY)
    for d in set().union(*digests.values()):
        assert reader.get(d) is None
    with db["evidence_admin"].connect() as conn:
        held = conn.execute(
            text("select count(*) from evidence.raw_row where original_row is not null")
        ).scalar()
        hashes = conn.execute(
            text("select count(*) from evidence.raw_row where original_sha256 is not null")
        ).scalar()
    assert held == hashes == 0


@pytest.fixture(scope="module")
def recorded_entries():
    merged: dict = {}
    for n in ("violations", "complaints", "buildings"):
        merged.update(load_cassette(FIXTURES / f"{n}_cassette.json"))
    return merged


def test_recorded_d9_snapshots_at_220_west_17th_survive_expiry(db, recorded_entries):
    res, address, _, b, violations, vbuffer = _d9(recorded_entries)
    _, vsaved = save_research_snapshot(
        db["writer"], res, address, [b, violations], vbuffer.items(), D9_SETTINGS
    )
    res, address, _, complaints, cbuffer, cb = _complaints_d9("west_17_220", recorded_entries)
    _, csaved = save_research_snapshot(
        db["writer"], res, address, [cb, complaints], cbuffer.items(), D9_SETTINGS
    )
    fingerprint = records_fingerprint(db)
    expire_everything(db)
    assert records_fingerprint(db) == fingerprint
    with db["app_reader"].connect() as conn:
        v = load_violation_result(conn, vsaved["hpd_violations"].fetch_id)
        c = load_complaint_result(conn, csaved["hpd_complaints"].fetch_id)
        vb = load_building_result(conn, v.buildings_fetch_id)
        cbl = load_building_result(conn, c.buildings_fetch_id)
    assert v.result == violations and c.result == complaints
    assert vb.result == b and cbl.result == cb
    resolved = v.result.resolved_bin
    for stored, n in ((v, 87), (c, 84)):
        moved = [
            r
            for r in stored.result.records
            if r.attribution_basis is AttributionBasis.HPD_BUILDING_ID
        ]
        # Moved to other buildings on the lot, never to the requested one.
        assert len(moved) == n
        assert {r.attribution.value for r in moved} == {"other_building_on_lot"}
        assert resolved not in {r.mapped_bin for r in moved}
        assert stored.result.building_id_attribution.upgraded == {"other_building_on_lot": n}
        assert stored.evidence and not any(e.held for e in stored.evidence.values())
    assert not any(e.held for e in vb.evidence.values())
