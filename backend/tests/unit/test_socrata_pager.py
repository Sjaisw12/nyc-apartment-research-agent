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


def test_select_is_sent_on_pages_for_row_id_paging():
    """Datasets without a unique business key page by Socrata's :id (Task 3)."""
    from nyc_apartment_agent.sources.socrata_pager import fetch_all
    from tests.hpd_helpers import FakeSocrata

    rows = [{":id": f"row-{i}", "v": "x"} for i in range(5)]
    fake = FakeSocrata(rows)
    with fake.client() as c:
        f = fetch_all(
            c,
            "d",
            where="1=1",
            order_by=":id",
            unique_key=":id",
            page_size=2,
            row_cap=100,
            select="*, :id",
        )
    assert f.complete and len(f.rows) == 5
    pages = [r for r in fake.calls if "$offset" in r.url.params]
    assert all(
        r.url.params["$select"] == "*, :id" and r.url.params["$order"] == ":id" for r in pages
    )
    count = fake.calls[0].url.params["$select"]
    assert "count(" in count  # the count call keeps its own select


# ---- retries and attempt records (Task 5 step 4, design 18.4) ---------------------------------


def run_retry(fake, **kw):
    with fake.client() as c:
        return fetch_all(
            c,
            "erm2-nwe9",
            where="bbl in ('2027540005')",
            order_by="violationid",
            unique_key="violationid",
            page_size=kw.pop("page_size", 100),
            row_cap=10_000,
            **kw,
        )


def test_every_attempt_is_recorded_even_when_rejected():
    fake = FakeSocrata(ROWS)
    fake.truth_at = {1: NEWER_TRUTH, 2: TRUTH}  # count TRUTH, data NEWER, then both TRUTH
    f = run_retry(fake, max_restarts=2)
    assert f.complete and f.provenance.restarts == 1
    assert [(a.count_header, a.page_headers, a.accepted) for a in f.attempts] == [
        (TRUTH, [NEWER_TRUTH], False),
        (TRUTH, [TRUTH], True),
    ]
    assert f.attempts[0].reasons == ["dataset_changed_during_retrieval"]
    assert f.provenance.data_as_of == TRUTH


def test_retry_on_other_reasons_is_opt_in():
    fake = FakeSocrata(ROWS)
    fake.short_at = {1}  # the first data response is one row short
    default = run_retry(fake, max_restarts=2)
    assert default.provenance.restarts == 0 and not default.complete
    fake = FakeSocrata(ROWS)
    fake.short_at = {1}
    f = run_retry(fake, max_restarts=2, retry_on=("short_page",))
    assert f.complete and f.provenance.restarts == 1 and len(f.attempts) == 2


def test_retries_stop_after_max_restarts_and_rows_are_never_merged():
    fake = FakeSocrata(ROWS)
    fake.short_at = {1, 3, 5}
    f = run_retry(fake, max_restarts=2, retry_on=("short_page",))
    assert f.provenance.restarts == 2 and len(f.attempts) == 3
    assert not any(a.accepted for a in f.attempts)
    assert len(f.rows) == 22  # only the last attempt's rows
    assert f.provenance.incomplete_reasons == ["short_page at offset 0: 22 < 23"]


def test_a_failed_retry_keeps_the_previous_attempt_and_says_so():
    fake = FakeSocrata(ROWS)
    fake.truth_at = {1: NEWER_TRUTH}
    fake.fail_at = {2: 500, 3: 500}  # the retry's count call fails (2 HTTP attempts)
    f = run_retry(fake, max_restarts=2)
    assert len(f.rows) == 23 and len(f.attempts) == 1
    assert f.provenance.incomplete_reasons == [
        "dataset_changed_during_retrieval",
        "retry_failed: SourceServerError",
    ]
