"""Pagination completeness and failure handling, on a SYNTHETIC Socrata server."""

import pytest

from nyc_apartment_agent.errors import SourceError
from nyc_apartment_agent.sources.socrata_pager import fetch_all
from tests.hpd_helpers import NEWER_TRUTH, TRUTH, FakeSocrata, row

ROWS = [row(i) for i in range(1, 24)]  # 23 rows -> pages of 10: 10, 10, 3


def run(fake, page_size=10, row_cap=10_000):
    with fake.client() as c:
        return fetch_all(
            c,
            "wvxf-dwi5",
            where="bbl='2027540005'",
            order_by="violationid",
            unique_key="violationid",
            page_size=page_size,
            row_cap=row_cap,
        )


def test_complete_fetch_over_several_pages():
    fake = FakeSocrata(ROWS)
    f = run(fake)
    assert f.complete
    assert [r["violationid"] for r in f.rows] == [str(i) for i in range(1, 24)]
    assert f.provenance.source_count == 23
    assert [p.rows for p in f.provenance.pages] == [0, 10, 10, 3]  # count call first
    assert f.provenance.data_as_of == TRUTH
    # Every page request is ordered by the unique key and bounded.
    for req in fake.calls[1:]:
        assert req.url.params["$order"] == "violationid"
        assert int(req.url.params["$limit"]) <= 10


def test_exact_multiple_of_page_size_makes_no_extra_request():
    fake = FakeSocrata(ROWS[:20])
    f = run(fake)
    assert f.complete and len(fake.calls) == 3  # count + 2 pages


def test_zero_rows_is_complete_and_makes_only_the_count_call():
    fake = FakeSocrata([])
    f = run(fake)
    assert f.complete and f.rows == [] and f.provenance.source_count == 0
    assert len(fake.calls) == 1


def test_count_failure_raises_source_error():
    fake = FakeSocrata(ROWS)
    fake.fail_at = {0: 503, 1: 503}  # both attempts of the count call
    with pytest.raises(SourceError):
        run(fake)


def test_transient_429_on_a_page_is_retried_and_still_complete():
    fake = FakeSocrata(ROWS)
    fake.fail_at = {2: 429}  # first attempt at page 2
    f = run(fake)
    assert f.complete and len(f.rows) == 23


def test_page_failure_keeps_partial_rows_and_says_so():
    fake = FakeSocrata(ROWS)
    fake.fail_at = {2: 500, 3: 500}  # page 2 fails on both attempts
    f = run(fake)
    assert not f.complete
    assert len(f.rows) == 10
    assert any(r.startswith("page_failed at offset 10") for r in f.provenance.incomplete_reasons)


def test_short_page_is_incomplete():
    fake = FakeSocrata(ROWS)
    fake.short_at = {2}
    f = run(fake)
    assert any(r.startswith("short_page") for r in f.provenance.incomplete_reasons)


def test_update_during_retrieval_restarts_once_then_succeeds():
    fake = FakeSocrata(ROWS)
    fake.truth_at = {2: NEWER_TRUTH}  # dataset refreshed before page 2 of the first pass
    f = run(fake)
    assert f.complete
    assert f.provenance.restarts == 1
    assert f.provenance.data_as_of == NEWER_TRUTH
    assert len(fake.calls) == 3 + 4  # first pass stopped at page 3, second pass full


def test_repeated_updates_during_retrieval_end_incomplete():
    fake = FakeSocrata(ROWS)
    fake.truth_at = {2: NEWER_TRUTH, 6: "Thu, 22 Oct 2026 14:00:00 GMT"}
    f = run(fake)
    assert "dataset_changed_during_retrieval" in f.provenance.incomplete_reasons
    assert f.provenance.restarts == 1
    assert f.provenance.data_as_of is None


def test_missing_freshness_header_is_incomplete():
    fake = FakeSocrata(ROWS, truth=None)
    f = run(fake)
    assert "freshness_header_missing" in f.provenance.incomplete_reasons


def test_freshness_header_missing_on_one_page_only_is_incomplete():
    fake = FakeSocrata(ROWS)
    fake.truth_at = {2: None}
    f = run(fake)
    assert not f.complete


def test_row_cap_is_reported_never_silent():
    fake = FakeSocrata(ROWS)
    f = run(fake, row_cap=15)
    assert len(f.rows) == 15
    assert f.provenance.incomplete_reasons == ["row_cap_exceeded: source has 23 rows, cap is 15"]


def test_duplicate_ids_across_pages_block_completeness():
    rows = ROWS[:10] + [row(5)] + ROWS[11:]
    f = run(FakeSocrata(rows))
    assert "duplicate_violationid_across_pages" in f.provenance.incomplete_reasons
