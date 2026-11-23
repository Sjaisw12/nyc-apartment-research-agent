"""Task 5 step 4: 311 raw evidence in PostgreSQL. SYNTHETIC rows with sentinel values.

311 public records are not stored until migration 0011 (step 5). Their private evidence
already goes through the Task 4 store unchanged. These tests check that the raw row's
source id is the k311_ token (so `records.evidence_identity` shows only tokens), that the
plain `unique_key` and the location live only inside `original_row`, that the evidence
verifies, and that expiry leaves a tombstone without them.
"""

import pytest
from sqlalchemy import text

from nyc_apartment_agent.records.evidence import verifies_original_row
from nyc_apartment_agent.records.models import DigestBasis
from nyc_apartment_agent.records.redaction import K311_PATTERN
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader, expire_due
from tests.db.test_evidence import KEY, NOW, age_raw_rows, write
from tests.unit.test_sr311 import retrieve, sr_row

pytestmark = pytest.mark.db
SENTINEL = "1 SENTINEL ROAD APT 4B"
UNIQUE_KEY = "60000001"


def _evidence():
    buffer = BufferedEvidenceStore()
    result, _ = retrieve([sr_row(1, incident_address=SENTINEL)], evidence=buffer, digest_key=KEY)
    return result, buffer


def test_311_evidence_is_stored_under_the_token_and_verifies(db):
    result, buffer = _evidence()
    (rec,) = result.records
    public = result.model_dump_json()
    assert SENTINEL not in public and UNIQUE_KEY not in public
    assert write(db["ingest_writer"], buffer.items()).inserted == 1
    got = PostgresEvidenceReader(db["evidence_admin"], KEY).get(rec.original_digest)
    assert got.dataset_id == "erm2-nwe9" and got.basis is DigestBasis.ORIGINAL_ROW
    assert got.source_record_id == rec.request_ref and K311_PATTERN.match(got.source_record_id)
    assert got.original_row["unique_key"] == UNIQUE_KEY
    assert got.original_row["incident_address"] == SENTINEL
    assert verifies_original_row(got, KEY)
    with db["ingest_writer"].connect() as conn:
        ids = conn.execute(
            text("select source_record_id from records.evidence_identity where dataset_id = :d"),
            {"d": "erm2-nwe9"},
        ).scalars()
        assert list(ids) == [rec.request_ref]
    with db["owner"].connect() as conn:
        days = conn.execute(
            text(
                "select extract(day from retain_until - fetched_at) from evidence.raw_row "
                "where original_digest = :d"
            ),
            {"d": rec.original_digest},
        ).scalar()
    assert days == 180


def test_311_evidence_expires_to_a_tombstone_without_key_or_location(db):
    result, buffer = _evidence()
    (rec,) = result.records
    write(db["ingest_writer"], buffer.items(), retention_days=1)
    age_raw_rows(db, 2)
    with db["evidence_retention"].begin() as conn:
        assert expire_due(conn, NOW) == 1
    with db["owner"].connect() as conn:
        row = conn.execute(
            text(
                "select original_row::text, source_record_id from evidence.raw_row "
                "where original_digest = :d"
            ),
            {"d": rec.original_digest},
        ).one()
    assert row[0] is None and row[1] == rec.request_ref
    assert PostgresEvidenceReader(db["evidence_admin"], KEY).get(rec.original_digest) is None
