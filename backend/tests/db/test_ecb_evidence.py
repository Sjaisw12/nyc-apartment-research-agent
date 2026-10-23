"""Task 5 step 3: ECB raw evidence in PostgreSQL. SYNTHETIC rows with sentinel values.

ECB public records are not stored until migration 0011 (step 5). The private evidence
for them already goes through the Task 4 store unchanged: keyed digests, the 180-day
retention cap and tombstones on expiry. These tests check that the respondent fields and
the withheld description live only there, verify there, and are removed on expiry.
"""

import pytest
from sqlalchemy import text

from nyc_apartment_agent.records.evidence import verifies_original_row
from nyc_apartment_agent.records.models import DigestBasis
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.evidence_pg import PostgresEvidenceReader, expire_due
from tests.db.test_evidence import KEY, NOW, age_raw_rows, write
from tests.unit.test_ecb_violations import ecb_row, retrieve

pytestmark = pytest.mark.db
SENTINEL = "MR. JOHN SENTINEL-ECB"


def _ecb_evidence():
    buffer = BufferedEvidenceStore()
    rows = [ecb_row(1, respondent_name=SENTINEL, violation_description=f"{SENTINEL} APT 4B")]
    result, _ = retrieve(rows, evidence=buffer)
    return result, buffer


def test_ecb_evidence_is_stored_verifiable_and_private(db):
    result, buffer = _ecb_evidence()
    (rec,) = result.records
    assert SENTINEL not in result.model_dump_json()
    report = write(db["ingest_writer"], buffer.items())
    assert report.inserted == 1
    got = PostgresEvidenceReader(db["evidence_admin"], KEY).get(rec.original_digest)
    assert got.dataset_id == "6bgk-3dad" and got.basis is DigestBasis.ORIGINAL_ROW
    assert got.original_row["respondent_name"] == SENTINEL
    assert verifies_original_row(got, KEY)
    with db["owner"].connect() as conn:
        days = conn.execute(
            text(
                "select extract(day from retain_until - fetched_at) from evidence.raw_row "
                "where original_digest = :d"
            ),
            {"d": rec.original_digest},
        ).scalar()
    assert days == 180


def test_ecb_evidence_expires_to_a_tombstone_without_the_respondent(db):
    result, buffer = _ecb_evidence()
    (rec,) = result.records
    write(db["ingest_writer"], buffer.items(), retention_days=1)
    age_raw_rows(db, 2)
    with db["evidence_retention"].begin() as conn:
        assert expire_due(conn, NOW) == 1
    with db["owner"].connect() as conn:
        row = conn.execute(
            text("select original_row::text from evidence.raw_row where original_digest = :d"),
            {"d": rec.original_digest},
        ).scalar()
    assert row is None
    assert PostgresEvidenceReader(db["evidence_admin"], KEY).get(rec.original_digest) is None
