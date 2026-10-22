"""The in-memory evidence buffer used during retrieval (no database)."""

import pytest

from nyc_apartment_agent.records.hpd_violations import retrieve_hpd_violations
from nyc_apartment_agent.records.models import DigestBasis, RawEvidence
from nyc_apartment_agent.records.redaction import canonical_sha256, keyed_digest
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore, EvidenceConflictError
from tests.hpd_helpers import FakeSocrata, resolution, row

KEY = b"synthetic-test-key"


def raw(r, basis=DigestBasis.ORIGINAL_ROW):
    return RawEvidence(
        dataset_id="wvxf-dwi5",
        source_record_id="1",
        original_sha256=canonical_sha256(r),
        original_digest=keyed_digest(r, KEY),
        redacted_sha256=canonical_sha256(r),
        redacted_fields=[],
        basis=basis,
        original_row=r,
        query_url="u",
        fetched_at="2026-10-22T01:00:00+00:00",
        data_as_of=None,
    )


def test_same_row_twice_is_held_once():
    b = BufferedEvidenceStore()
    b.put(raw({"a": "1"}))
    b.put(raw({"a": "1"}))
    assert len(b) == 1 and b.get(raw({"a": "1"}).original_digest) is not None


def test_different_content_under_one_digest_is_refused():
    b = BufferedEvidenceStore()
    first = raw({"a": "1"})
    b.put(first)
    with pytest.raises(EvidenceConflictError):
        b.put(first.model_copy(update={"original_row": {"a": "2"}}))


def test_adapters_fill_the_buffer_and_nothing_else():
    b = BufferedEvidenceStore()
    with FakeSocrata([row(1), row(2)]).client() as c:
        result = retrieve_hpd_violations(resolution(), c, evidence=b, digest_key=KEY, page_size=10)
    assert {r.original_digest for r in result.records} == {i.original_digest for i in b.items()}
    b.clear()
    assert len(b) == 0
