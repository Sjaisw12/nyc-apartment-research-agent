"""Bounded, verifiable pagination over a Socrata (SODA) dataset.

A fetch is called complete only when all of these hold:
1. count(*) for the same filter succeeded,
2. every page succeeded,
3. rows fetched == count, and no page came back short before the end,
4. the ordering key is unique across the fetched rows,
5. X-SODA2-Truth-Last-Modified is present and identical on the count response and
   on every page (otherwise the dataset may have been refreshed mid-fetch, and
   offsets would no longer line up).

If (5) fails, the whole fetch restarts once; if it fails again, the result is
incomplete. Rows beyond `row_cap` are never fetched; the result says so.

A failure on the count call raises (nothing was retrieved: "source unavailable").
A failure on a later page returns the partial rows with a reason (never silently).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..errors import SourceError, SourceResponseError
from ..http_client import SourceHttpClient, SourceResponse
from ..records.models import PageRecord, RetrievalProvenance

logger = logging.getLogger(__name__)

TRUTH_HEADER = "x-soda2-truth-last-modified"


@dataclass
class PagedFetch:
    rows: list[dict[str, Any]]
    provenance: RetrievalProvenance
    page_urls: list[str] = field(default_factory=list)  # URL that returned rows[i]'s page
    row_page: list[int] = field(default_factory=list)  # page index for each row

    @property
    def complete(self) -> bool:
        return not self.provenance.incomplete_reasons


def _page_record(index: int, resp: SourceResponse, rows: int) -> PageRecord:
    return PageRecord(
        index=index,
        url=resp.url,
        rows=rows,
        fetched_at=resp.fetched_at,
        attempts=resp.attempts,
        truth_last_modified=resp.headers.get(TRUTH_HEADER),
        last_modified=resp.headers.get("last-modified"),
        request_id=resp.headers.get("x-socrata-requestid"),
    )


def _rows(resp: SourceResponse) -> list[dict[str, Any]]:
    if not isinstance(resp.data, list) or not all(isinstance(r, dict) for r in resp.data):
        raise SourceResponseError(resp.source, resp.url, "expected a JSON array of objects")
    return resp.data


def _count(client: SourceHttpClient, resource: str, where: str) -> tuple[int, SourceResponse]:
    resp = client.get_json(resource, params={"$select": "count(*) AS n", "$where": where})
    rows = _rows(resp)
    try:
        n = int(rows[0]["n"])
    except (IndexError, KeyError, ValueError) as exc:
        raise SourceResponseError(resp.source, resp.url, f"bad count response: {rows!r}") from exc
    if n < 0:
        raise SourceResponseError(resp.source, resp.url, f"negative count: {n}")
    return n, resp


def fetch_all(
    client: SourceHttpClient,
    dataset_id: str,
    *,
    where: str,
    order_by: str,
    unique_key: str,
    page_size: int,
    row_cap: int,
    required_fields: tuple[str, ...] = (),
    max_restarts: int = 1,
    select: str | None = None,
) -> PagedFetch:
    """Fetch every row matching `where`, ordered by `order_by` (must be unique).

    For datasets without a unique business key, page by Socrata's row id: pass
    `order_by=":id"`, `unique_key=":id"` and `select="*, :id"` so the id comes back in
    each row and its uniqueness can be checked like any other key.
    """
    if page_size < 1 or row_cap < 1:
        raise ValueError("page_size and row_cap must be positive")
    resource = f"/resource/{dataset_id}.json"
    restarts = 0
    while True:
        result = _fetch_once(
            client, resource, dataset_id, where, order_by, unique_key, page_size, row_cap, select
        )
        changed = "dataset_changed_during_retrieval" in result.provenance.incomplete_reasons
        if changed and restarts < max_restarts:
            restarts += 1
            logger.warning("%s changed during retrieval; restarting (%d)", dataset_id, restarts)
            continue
        result.provenance.restarts = restarts
        _check_schema(result, required_fields)
        logger.info(
            "%s: count=%s fetched=%d pages=%d complete=%s",
            dataset_id,
            result.provenance.source_count,
            len(result.rows),
            len(result.provenance.pages) - 1,
            result.complete,
        )
        return result


def _fetch_once(
    client: SourceHttpClient,
    resource: str,
    dataset_id: str,
    where: str,
    order_by: str,
    unique_key: str,
    page_size: int,
    row_cap: int,
    select: str | None = None,
) -> PagedFetch:
    prov = RetrievalProvenance(
        dataset_id=dataset_id, where=where, order_by=order_by, page_size=page_size, row_cap=row_cap
    )
    # Raises SourceError if the source is down: nothing retrieved, caller reports unavailable.
    count, count_resp = _count(client, resource, where)
    prov.source_count = count
    prov.pages.append(_page_record(-1, count_resp, 0))
    first_truth = count_resp.headers.get(TRUTH_HEADER)

    fetch = PagedFetch(rows=[], provenance=prov)
    reasons: list[str] = []
    target = min(count, row_cap)
    if count > row_cap:
        reasons.append(f"row_cap_exceeded: source has {count} rows, cap is {row_cap}")

    offset = 0
    index = 0
    while offset < target:
        limit = min(page_size, target - offset)
        try:
            params = {"$where": where, "$order": order_by, "$limit": limit, "$offset": offset}
            if select:
                params = {"$select": select, **params}
            resp = client.get_json(resource, params=params)
            page = _rows(resp)
        except SourceError as exc:
            reasons.append(f"page_failed at offset {offset}: {type(exc).__name__}")
            prov.error = str(exc)
            break
        prov.pages.append(_page_record(index, resp, len(page)))
        fetch.page_urls.append(resp.url)
        fetch.rows.extend(page)
        fetch.row_page.extend([index] * len(page))
        if resp.headers.get(TRUTH_HEADER) != first_truth:
            break  # dataset version changed (or header vanished): stop now, judged below
        if len(page) > limit:
            reasons.append(f"page_too_long at offset {offset}: {len(page)} > {limit}")
            break
        if len(page) < limit:
            reasons.append(f"short_page at offset {offset}: {len(page)} < {limit}")
            break
        offset += len(page)
        index += 1

    # Freshness: the dataset version must be known and identical on every response.
    truths = [p.truth_last_modified for p in prov.pages]
    if any(t is None for t in truths):
        reasons.append("freshness_header_missing")
    elif len(set(truths)) > 1:
        reasons.append("dataset_changed_during_retrieval")
    else:
        prov.data_as_of = first_truth

    # Completeness against the count, and a unique ordering key.
    if not any(r.startswith(("page_failed", "short_page", "page_too_long")) for r in reasons):
        if len(fetch.rows) != target:
            reasons.append(f"row_count_mismatch: fetched {len(fetch.rows)}, expected {target}")
    keys = [r.get(unique_key) for r in fetch.rows]
    if any(k is None for k in keys):
        reasons.append(f"missing_{unique_key}")
    if len({k for k in keys if k is not None}) != len([k for k in keys if k is not None]):
        # Not fatal by itself (the normalizer collapses or flags duplicates), but paging
        # by a non-unique key can skip rows, so the fetch cannot be called complete.
        reasons.append(f"duplicate_{unique_key}_across_pages")

    prov.rows_fetched = len(fetch.rows)
    prov.incomplete_reasons = reasons
    return fetch


def _check_schema(fetch: PagedFetch, required_fields: tuple[str, ...]) -> None:
    """Socrata omits null fields from rows, so schema drift is checked from data rows only
    as a warning. A field missing from every row is reported, not treated as an error."""
    if not required_fields or not fetch.rows:
        return
    seen: set[str] = set()
    for row in fetch.rows:
        seen.update(row)
    missing = sorted(set(required_fields) - seen)
    if missing:
        fetch.provenance.warnings.append(f"fields_absent_from_all_rows: {missing}")
