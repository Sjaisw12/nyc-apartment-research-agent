"""Shared builders for Phase 2 HPD tests. Everything here is SYNTHETIC test input."""

from __future__ import annotations

import json

import httpx

from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.resolver.models import (
    BinEvidence,
    Outcome,
    ParsedInput,
    ResolutionLevel,
    ResolutionResult,
    ResolvedIdentity,
)
from nyc_apartment_agent.sources.clients import socrata_client

TRUTH = "Tue, 20 Oct 2026 14:55:26 GMT"
NEWER_TRUTH = "Wed, 21 Oct 2026 14:51:46 GMT"


def _parsed() -> ParsedInput:
    return ParsedInput(
        raw="synthetic",
        house_number="1",
        street="TEST",
        street_normalized="TEST",
        borough="Bronx",
        zip_code=None,
        state=None,
        notes=[],
    )


def resolution(
    bbl: str = "2027540005",
    bin_: str | None = "2006185",
    level: ResolutionLevel | None = ResolutionLevel.BUILDING,
    outcome: Outcome = Outcome.VERIFIED,
) -> ResolutionResult:
    """A synthetic ResolutionResult (the resolver itself is tested in Phase 1)."""
    building = None
    if outcome is Outcome.VERIFIED:
        building = ResolvedIdentity(
            bbl=bbl,
            bin=bin_,
            bin_evidence=BinEvidence.CROSS_VERIFIED
            if level is ResolutionLevel.BUILDING
            else BinEvidence.PAD_ONLY,
            label="synthetic",
            aliases=[],
            borough="Bronx",
            borough_source="stated",
        )
    return ResolutionResult(
        outcome=outcome,
        explanation="synthetic",
        reasons=[],
        input=_parsed(),
        resolution_level=level if outcome is Outcome.VERIFIED else None,
        building=building,
    )


def row(vid: int, **overrides) -> dict:
    """A synthetic violation row shaped like wvxf-dwi5 (field names from X-SODA2-Fields)."""
    base = {
        "violationid": str(vid),
        "boroid": "2",
        "block": "2754",
        "lot": "5",
        "bbl": "2027540005",
        "bin": "2006185",
        "class": "B",
        "inspectiondate": "2026-01-15T00:00:00.000",
        "novdescription": "SECTION 27-2005 ADM CODE REPAIR THE BROKEN PLASTER AT PUBLIC HALL",
        "currentstatus": "NOV SENT OUT",
        "violationstatus": "Open",
        "rentimpairing": "N",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}  # Socrata omits nulls


class FakeSocrata:
    """Serves count(*) and $limit/$offset pages over a list of rows. Synthetic.

    Hooks let a test inject failures or a dataset refresh at a given call number.
    """

    def __init__(self, rows, truth=TRUTH):
        self.rows = rows
        self.truth = truth
        self.calls: list[httpx.Request] = []
        self.fail_at: dict[int, int] = {}  # call number -> HTTP status
        self.truth_at: dict[int, str | None] = {}  # call number -> header value from then on
        self.short_at: set[int] = set()  # calls that return one row fewer

    def handler(self, request: httpx.Request) -> httpx.Response:
        n = len(self.calls)
        self.calls.append(request)
        for k in sorted(self.truth_at):
            if n >= k:
                self.truth = self.truth_at[k]
        if n in self.fail_at:
            return httpx.Response(self.fail_at[n], text="synthetic failure")
        headers = {"x-soda2-truth-last-modified": self.truth} if self.truth else {}
        params = request.url.params
        if "count(" in params.get("$select", ""):
            return httpx.Response(200, json=[{"n": str(len(self.rows))}], headers=headers)
        offset, limit = int(params["$offset"]), int(params["$limit"])
        page = self.rows[offset : offset + limit]
        if n in self.short_at:
            page = page[:-1]
        return httpx.Response(200, content=json.dumps(page), headers=headers)

    def client(self):
        settings = Settings(_env_file=None, http_max_attempts=2)
        c = socrata_client(settings, transport=httpx.MockTransport(self.handler))
        c._sleep = lambda _s: None  # no real waiting in tests
        return c


DISPOSITION_ISSUED = (
    "The Department of Housing Preservation and Development inspected the following "
    "conditions. Violations were issued. Information about specific violations is "
    "available at www.nyc.gov/hpd."
)


def complaint_row(pid: int, cid: int = 100, **overrides) -> dict:
    """A synthetic problem row shaped like ygpa-z7cr (field names from X-SODA2-Fields)."""
    base = {
        "problem_id": str(pid),
        "complaint_id": str(cid),
        "building_id": "1",
        "borough": "BRONX",
        "block": "2754",
        "lot": "5",
        "bbl": "2027540005",
        "bin": "2006185",
        "apartment": "1A",
        "received_date": "2026-01-15T10:00:00.000",
        "complaint_status": "CLOSE",
        "complaint_status_date": "2026-01-20T05:00:00.000",
        "problem_status": "CLOSE",
        "problem_status_date": "2026-01-19T12:00:00.000",
        "unit_type": "APARTMENT",
        "space_type": "BATHROOM",
        "type": "NON EMERGENCY",
        "major_category": "PLUMBING",
        "minor_category": "WATER-LEAKS",
        "problem_code": "SLOW LEAK",
        "status_description": DISPOSITION_ISSUED,
        "problem_duplicate_flag": "N",
        "complaint_anonymous_flag": "N",
        "unique_key": "99999999",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}  # Socrata omits nulls
