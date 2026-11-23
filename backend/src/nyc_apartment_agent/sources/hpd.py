"""HPD datasets on NYC Open Data (Socrata SODA API).

- Housing Maintenance Code Violations: wvxf-dwi5
- Housing Maintenance Code Complaints and Problems: ygpa-z7cr

Queries are bounded ($limit) and ordered by a stable key so paging is repeatable.
The BBL is validated before it is placed in a SoQL filter.
"""

from __future__ import annotations

from typing import Any

from ..errors import SourceResponseError
from ..http_client import SourceHttpClient, SourceResponse
from ..identifiers import normalize_bbl

VIOLATIONS_DATASET = "wvxf-dwi5"
COMPLAINT_PROBLEMS_DATASET = "ygpa-z7cr"
MAX_PAGE = 1000

# Fields we rely on downstream. A smoke test fails if any disappear from the source.
VIOLATION_FIELDS = (
    "violationid",
    "bbl",
    "bin",
    "boroid",
    "block",
    "lot",
    "class",
    "inspectiondate",
    "novdescription",
    "currentstatus",
    "violationstatus",
)
COMPLAINT_PROBLEM_FIELDS = (
    "complaint_id",
    "problem_id",
    "bbl",
    "bin",
    "received_date",
    "major_category",
    "minor_category",
    "problem_status",
    "complaint_status",
)


def _resource(dataset: str) -> str:
    return f"/resource/{dataset}.json"


def _rows(resp: SourceResponse) -> list[dict[str, Any]]:
    if not isinstance(resp.data, list) or not all(isinstance(r, dict) for r in resp.data):
        raise SourceResponseError(resp.source, resp.url, "expected a JSON array of objects")
    return resp.data


def count_by_bbl(client: SourceHttpClient, dataset: str, bbl: str) -> tuple[int, SourceResponse]:
    bbl = normalize_bbl(bbl)
    resp = client.get_json(
        _resource(dataset), params={"$select": "count(*) AS n", "$where": f"bbl='{bbl}'"}
    )
    rows = _rows(resp)
    try:
        return int(rows[0]["n"]), resp
    except (IndexError, KeyError, ValueError) as exc:
        raise SourceResponseError(resp.source, resp.url, f"bad count response: {rows!r}") from exc


def rows_by_bbl(
    client: SourceHttpClient,
    dataset: str,
    bbl: str,
    order_by: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], SourceResponse]:
    bbl = normalize_bbl(bbl)
    if order_by not in ("violationid", "problem_id", "inspectiondate DESC", "received_date DESC"):
        raise ValueError(f"unsupported order_by: {order_by!r}")
    limit = max(1, min(limit, MAX_PAGE))
    resp = client.get_json(
        _resource(dataset),
        params={
            "$where": f"bbl='{bbl}'",
            "$order": order_by,
            "$limit": limit,
            "$offset": max(0, offset),
        },
    )
    return _rows(resp), resp


def get_violations(client: SourceHttpClient, bbl: str, limit: int = 50, offset: int = 0):
    return rows_by_bbl(client, VIOLATIONS_DATASET, bbl, "violationid", limit, offset)


def get_complaint_problems(client: SourceHttpClient, bbl: str, limit: int = 50, offset: int = 0):
    return rows_by_bbl(client, COMPLAINT_PROBLEMS_DATASET, bbl, "problem_id", limit, offset)


def missing_fields(rows: list[dict[str, Any]], expected: tuple[str, ...]) -> set[str]:
    """Expected fields that appear in none of the rows.

    Socrata omits null fields from JSON rows, so we check across all rows, not each row.
    """
    seen: set[str] = set()
    for row in rows:
        seen.update(row)
    return set(expected) - seen
