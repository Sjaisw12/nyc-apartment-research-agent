"""The five recorded Task 5 addresses, replayed offline for the step 5 database tests.

For each address of the committed recordings (resolver and HPD building records from
tests/fixtures/hpd/buildings_cassette.json; Footprints, DOB complaints, ECB and 311 from
tests/fixtures/dob311/, 2026-10-23 04:51Z), this rebuilds the three Task 5 results
exactly as the step 2-4 replay tests do, with one evidence buffer per address. The
recordings are not changed. 311 rows get distinct synthetic keys in memory only (as in
tests/unit/test_sr311_cassette.py): those keys and their tokens are test values, every
311 digest has basis sanitized_row, and nothing here is production evidence.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from nyc_apartment_agent.cassette import ReplayTransport, load_cassette
from nyc_apartment_agent.config import Settings
from nyc_apartment_agent.records.dob_complaints import retrieve_dob_complaints
from nyc_apartment_agent.records.dob_models import DobComplaintResult
from nyc_apartment_agent.records.ecb_models import EcbViolationResult
from nyc_apartment_agent.records.ecb_violations import retrieve_ecb_violations
from nyc_apartment_agent.records.hpd_buildings import retrieve_hpd_buildings
from nyc_apartment_agent.records.sr311 import retrieve_service_requests
from nyc_apartment_agent.records.sr311_models import ServiceRequestResult
from nyc_apartment_agent.resolver import resolve_address
from nyc_apartment_agent.resolver.models import ResolutionResult
from nyc_apartment_agent.sources.clients import geosearch_client, socrata_client
from nyc_apartment_agent.storage.evidence_buffer import BufferedEvidenceStore
from nyc_apartment_agent.storage.repository import save_research_snapshot
from tests.unit.test_sr311_cassette import _with_synthetic_keys

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HPD_META = json.loads((FIXTURES / "hpd" / "buildings_cassette.meta.json").read_text("utf-8"))
DOB_META = json.loads((FIXTURES / "dob311" / "cassettes.meta.json").read_text("utf-8"))
TODAY = dt.date(2026, 10, 23)
KEY_TEXT = "task5-step5-recorded-key"
KEY = KEY_TEXT.encode()
SETTINGS = Settings(_env_file=None, evidence_digest_key=KEY_TEXT, http_max_attempts=1)
ADDRESSES = tuple(HPD_META["addresses"])

# Accepted step 2-4 results for the recordings (records, status). Never changed.
DOB_EXPECTED = {
    "west_farms_1134": (12, "records_found"),
    "west_17_220": (46, "records_found"),
    "west_farms_1217": (0, "unsupported_join"),
}
SR311_EXPECTED = {
    "west_farms_1134": (89, "incomplete_pagination"),
    "west_17_220": (89, "records_found"),
    "west_farms_1217": (32, "records_found"),
    "fifth_ave_350": (485, "records_found"),
    "acp_1845": (250, "incomplete_pagination"),
}
ECB_EXPECTED_TOTAL = {  # recorded acceptance targets (step 3)
    "west_farms_1134": 3,
    "west_17_220": 4,
    "west_farms_1217": 12,
    "fifth_ave_350": 241,
    "acp_1845": 52,
}


@dataclass
class Recorded:
    key: str
    address: str
    resolution: ResolutionResult
    dob: DobComplaintResult
    ecb: EcbViolationResult
    sr311: ServiceRequestResult
    buffer: BufferedEvidenceStore = field(default_factory=BufferedEvidenceStore)

    @property
    def results(self):
        return [self.dob, self.ecb, self.sr311]


@cache
def _entries():
    hpd = load_cassette(FIXTURES / "hpd" / "buildings_cassette.json")
    footprints = load_cassette(FIXTURES / "dob311" / "footprints_cassette.json")
    dob = {**footprints, **load_cassette(FIXTURES / "dob311" / "dob_complaints_cassette.json")}
    ecb = {**footprints, **load_cassette(FIXTURES / "dob311" / "dob_ecb_cassette.json")}
    sr = {
        **footprints,
        **_with_synthetic_keys(load_cassette(FIXTURES / "dob311" / "sr311_cassette.json")),
    }
    return hpd, dob, ecb, sr


@cache
def build(key: str) -> Recorded:
    """Built once per test session; callers must not modify the results."""
    hpd_e, dob_e, ecb_e, sr_e = _entries()
    address = HPD_META["addresses"][key]["address"]
    buffer = BufferedEvidenceStore()
    t_hpd = ReplayTransport(hpd_e)
    t_dob, t_ecb, t_sr = ReplayTransport(dob_e), ReplayTransport(ecb_e), ReplayTransport(sr_e)
    page = DOB_META["page_size"]
    common = {"digest_key": KEY, "evidence": buffer, "today": TODAY, "page_size": page}
    with (
        geosearch_client(SETTINGS, t_hpd) as g,
        socrata_client(SETTINGS, t_hpd) as s_hpd,
        socrata_client(SETTINGS, t_dob) as s_dob,
        socrata_client(SETTINGS, t_ecb) as s_ecb,
        socrata_client(SETTINGS, t_sr) as s_sr,
    ):
        res = resolve_address(address, g, s_hpd)
        hpd = retrieve_hpd_buildings(
            res, s_hpd, page_size=HPD_META["page_size"], digest_key=KEY, today=TODAY
        )
        dob = retrieve_dob_complaints(res, s_dob, hpd_buildings=hpd, **common)
        ecb = retrieve_ecb_violations(res, s_ecb, hpd_buildings=hpd, **common)
        sr = retrieve_service_requests(res, s_sr, settings=SETTINGS, **common)
    assert t_hpd.misses == [] and t_dob.misses == [] and t_ecb.misses == [], key
    assert len(t_sr.misses) == 1 and "agency+IS+NULL" in t_sr.misses[0], key  # unrecorded
    return Recorded(key, address, res, dob, ecb, sr, buffer)


def save_recorded(engine, rec: Recorded):
    """The three results of one address in one snapshot, as the given login."""
    return save_research_snapshot(
        engine, rec.resolution, rec.address, rec.results, rec.buffer.items(), SETTINGS
    )
