"""Resolve an address and collect every supported source into one EvidenceBundle.

Two modes share the same code path (resolver + Phase 2 adapters):

- live:  real HTTP clients against GeoSearch and NYC Open Data.
- demo:  the committed, sanitized cassettes under backend/tests/fixtures are replayed through
         the same adapters. No network, no credentials. Only the recorded addresses have
         records; any other address is reported as outside the demo dataset, never guessed.

Nothing here makes a claim about a building. It only gathers typed results; the report
builder decides what may be said.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx

from ..cassette import ReplayTransport, load_cassette
from ..config import Settings, get_settings
from ..records.admin_models import (
    BuildingSourceResult,
    ContactSourceResult,
    RegistrationSourceResult,
)
from ..records.complaint_models import ComplaintSourceResult
from ..records.dob_complaints import retrieve_dob_complaints
from ..records.dob_models import DobComplaintResult
from ..records.ecb_models import EcbViolationResult
from ..records.ecb_violations import retrieve_ecb_violations
from ..records.hpd_buildings import retrieve_hpd_buildings
from ..records.hpd_complaints import retrieve_hpd_complaints
from ..records.hpd_registrations import retrieve_hpd_registrations
from ..records.hpd_violations import retrieve_hpd_violations
from ..records.models import SourceResult
from ..records.sr311 import retrieve_service_requests
from ..records.sr311_models import ServiceRequestResult
from ..resolver import Outcome, ResolutionResult, resolve_address
from ..resolver.models import BinEvidence, ResolutionLevel, ResolvedIdentity
from ..sources.clients import geosearch_client, socrata_client

logger = logging.getLogger(__name__)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"

# The five addresses with complete recorded evidence (Phase 2 acceptance set).
DEMO_ADDRESSES: dict[str, str] = {
    "west_farms_1134": "1134 West Farms Rd, Bronx, NY 10459",
    "west_17_220": "220 West 17th Street, Manhattan",
    "west_farms_1217": "1217 West Farms Road, Bronx",
    "fifth_ave_350": "350 5th Avenue, New York, NY 10118",
    "acp_1845": "1845 Adam Clayton Powell Jr Blvd, Manhattan",
}


@dataclass
class EvidenceBundle:
    """Everything one investigation gathered, before any report wording."""

    address: str
    mode: str  # "live" | "demo"
    resolution: ResolutionResult
    started_at: str
    elapsed_seconds: float = 0.0
    in_demo_dataset: bool = True
    selected_option: dict | None = None  # set when the user picked an ambiguous option
    hpd_buildings: BuildingSourceResult | None = None
    hpd_violations: SourceResult | None = None
    hpd_complaints: ComplaintSourceResult | None = None
    hpd_registrations: RegistrationSourceResult | None = None
    hpd_contacts: ContactSourceResult | None = None
    dob_complaints: DobComplaintResult | None = None
    ecb_violations: EcbViolationResult | None = None
    sr311: ServiceRequestResult | None = None
    recorded_at: dict[str, str] = field(default_factory=dict)  # demo: per-source capture time
    external_requests: int = 0
    errors: list[str] = field(default_factory=list)


# ---- user-selected option for an ambiguous address ------------------------------------


def select_option(resolution: ResolutionResult, bbl: str, bin_: str | None) -> ResolutionResult:
    """Turn an ambiguous result into a verified one for the option the user picked.

    Only an option the resolver itself offered can be chosen. The level follows the
    resolver's own BIN evidence: building level only for a cross-verified BIN, else tax lot.
    """
    if resolution.outcome is not Outcome.AMBIGUOUS:
        return resolution
    chosen: ResolvedIdentity | None = None
    for opt in resolution.options:
        if opt.bbl == bbl and (bin_ is None or opt.bin == bin_):
            chosen = opt
            break
    if chosen is None:
        return resolution
    level = (
        ResolutionLevel.BUILDING
        if chosen.bin_evidence is BinEvidence.CROSS_VERIFIED and chosen.bin
        else ResolutionLevel.TAX_LOT
    )
    data = resolution.model_dump()
    data.update(
        outcome=Outcome.VERIFIED,
        explanation=(
            "You chose this building from the options the resolver offered. "
            + resolution.explanation
        ),
        resolution_level=level,
        building=chosen.model_dump(),
        options=[],
    )
    data.pop("permitted_join_keys", None)
    data.pop("building_specific_findings_allowed", None)
    result = ResolutionResult.model_validate(data)
    result.provenance["user_selected_option"] = {"bbl": chosen.bbl, "bin": chosen.bin}
    return result


# ---- demo (replay) ---------------------------------------------------------------------


def _meta(rel: str) -> dict:
    return json.loads((FIXTURES / rel).read_text("utf-8"))


@lru_cache(maxsize=1)
def _demo_cassettes() -> dict:
    """Load every committed cassette once (each is hash-checked by load_cassette)."""
    hpd = {
        name: load_cassette(FIXTURES / "hpd" / f"{name}_cassette.json")
        for name in ("violations", "complaints", "buildings", "registrations", "contacts")
    }
    footprints = load_cassette(FIXTURES / "dob311" / "footprints_cassette.json")
    dob = {**footprints, **load_cassette(FIXTURES / "dob311" / "dob_complaints_cassette.json")}
    ecb = {**footprints, **load_cassette(FIXTURES / "dob311" / "dob_ecb_cassette.json")}
    sr = {
        **footprints,
        **_with_synthetic_311_keys(load_cassette(FIXTURES / "dob311" / "sr311_cassette.json")),
    }
    resolver = load_cassette(FIXTURES / "benchmark" / "resolver_cassette.json")
    meta = {
        name: _meta(f"hpd/{name}_cassette.meta.json")
        for name in ("violations", "complaints", "buildings", "registrations", "contacts")
    }
    meta["dob311"] = _meta("dob311/cassettes.meta.json")
    return {
        "hpd": hpd,
        "dob": dob,
        "ecb": ecb,
        "sr311": sr,
        "resolver": {**resolver, **hpd["buildings"]},
        "meta": meta,
    }


def _with_synthetic_311_keys(entries: dict) -> dict:
    """The committed 311 rows carry unique_key = [WITHHELD] plus a fixture_row_ref.

    The pager needs distinct keys, so (in memory only, exactly as the Phase 2 replay test
    does) each withheld key becomes a synthetic number built from the fixture ref. These
    are demo values: never real 311 identifiers, and never compared with original evidence.
    """
    out = {}
    for url, entry in entries.items():
        if "%24order=unique_key" not in url:
            out[url] = entry
            continue
        rows = json.loads(entry["body"])
        for r in rows:
            ref = r.pop("fixture_row_ref", None)
            if r.get("unique_key") == "[WITHHELD]" and ref:
                r["unique_key"] = str(900_000_000 + int(ref.removeprefix("FX")))
        out[url] = {**entry, "body": json.dumps(rows)}
    return out


def demo_key_for(resolution: ResolutionResult) -> str | None:
    """Which recorded address (if any) has evidence for this verified lot."""
    if resolution.outcome is not Outcome.VERIFIED or resolution.building is None:
        return None
    meta = _demo_cassettes()["meta"]["violations"]["addresses"]
    for key, info in meta.items():
        if info["bbl"] == resolution.building.bbl and info.get("bin") == resolution.building.bin:
            return key
    return None


def _demo_today(meta: dict) -> dt.date:
    return dt.date.fromisoformat(meta["recorded_at"][:10])


def resolve_demo(address: str, *, selected: dict | None = None) -> EvidenceBundle:
    """Stage 1 (demo): resolve against the recorded GeoSearch/Footprints responses."""
    t0 = time.monotonic()
    c = _demo_cassettes()
    settings = Settings(_env_file=None, http_max_attempts=1)
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    t_res = ReplayTransport(c["resolver"])
    with geosearch_client(settings, t_res) as g, socrata_client(settings, t_res) as s:
        resolution = resolve_address(address, g, s)
    if selected:
        resolution = select_option(resolution, selected.get("bbl", ""), selected.get("bin"))
    bundle = EvidenceBundle(
        address=address,
        mode="demo",
        resolution=resolution,
        started_at=started,
        selected_option=selected,
    )
    bundle.external_requests = 0  # replayed, nothing left the process
    if t_res.misses and resolution.outcome is Outcome.SOURCE_UNAVAILABLE:
        # The resolver asked for something that was never recorded: outside the demo set.
        bundle.in_demo_dataset = False
    elif resolution.outcome is Outcome.VERIFIED and demo_key_for(resolution) is None:
        bundle.in_demo_dataset = False
    bundle.elapsed_seconds = round(time.monotonic() - t0, 3)
    return bundle


def gather_demo(bundle: EvidenceBundle, *, digest_key: bytes = b"demo-replay-key") -> None:
    """Stage 2 (demo): replay every Phase 2 adapter for a recorded, verified address."""
    t0 = time.monotonic()
    resolution = bundle.resolution
    if resolution.outcome is not Outcome.VERIFIED or demo_key_for(resolution) is None:
        return
    c = _demo_cassettes()
    meta = c["meta"]
    settings = Settings(_env_file=None, http_max_attempts=1)

    def hpd_client(name: str):
        return socrata_client(settings, ReplayTransport(c["hpd"][name]))

    with hpd_client("buildings") as s:
        bundle.hpd_buildings = retrieve_hpd_buildings(
            resolution,
            s,
            page_size=meta["buildings"]["page_size"],
            digest_key=digest_key,
            today=_demo_today(meta["buildings"]),
        )
    with hpd_client("violations") as s:
        bundle.hpd_violations = retrieve_hpd_violations(
            resolution,
            s,
            page_size=meta["violations"]["page_size"],
            digest_key=digest_key,
            today=_demo_today(meta["violations"]),
        )
    with hpd_client("complaints") as s:
        bundle.hpd_complaints = retrieve_hpd_complaints(
            resolution,
            s,
            page_size=meta["complaints"]["page_size"],
            digest_key=digest_key,
            today=_demo_today(meta["complaints"]),
        )
    with hpd_client("registrations") as s:
        bundle.hpd_registrations = retrieve_hpd_registrations(
            resolution,
            s,
            buildings=bundle.hpd_buildings,
            page_size=meta["registrations"]["page_size"],
            digest_key=digest_key,
            today=_demo_today(meta["registrations"]),
        )
    dob_today = _demo_today(meta["dob311"])
    page = meta["dob311"]["page_size"]
    with socrata_client(settings, ReplayTransport(c["dob"])) as s:
        bundle.dob_complaints = retrieve_dob_complaints(
            resolution,
            s,
            hpd_buildings=bundle.hpd_buildings,
            page_size=page,
            digest_key=digest_key,
            today=dob_today,
        )
    with socrata_client(settings, ReplayTransport(c["ecb"])) as s:
        bundle.ecb_violations = retrieve_ecb_violations(
            resolution,
            s,
            hpd_buildings=bundle.hpd_buildings,
            page_size=page,
            digest_key=digest_key,
            today=dob_today,
        )
    with socrata_client(settings, ReplayTransport(c["sr311"])) as s:
        bundle.sr311 = retrieve_service_requests(
            resolution, s, page_size=page, digest_key=digest_key, today=dob_today
        )
    bundle.recorded_at = {
        "hpd_violations": meta["violations"]["recorded_at"],
        "hpd_complaints": meta["complaints"]["recorded_at"],
        "hpd_buildings": meta["buildings"]["recorded_at"],
        "hpd_registrations": meta["registrations"]["recorded_at"],
        "dob_complaints": meta["dob311"]["recorded_at"],
        "ecb_violations": meta["dob311"]["recorded_at"],
        "sr311": meta["dob311"]["recorded_at"],
    }
    bundle.elapsed_seconds = round(bundle.elapsed_seconds + time.monotonic() - t0, 3)


def collect_demo(address: str, *, selected: dict | None = None) -> EvidenceBundle:
    bundle = resolve_demo(address, selected=selected)
    gather_demo(bundle)
    return bundle


# ---- live ------------------------------------------------------------------------------


class _CountingTransport(httpx.HTTPTransport):
    """Counts outgoing requests and refuses any beyond the investigation's budget."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.count >= self.limit:
            raise httpx.ConnectError("external request budget reached", request=request)
        self.count += 1
        return super().handle_request(request)


def resolve_live(
    address: str, *, selected: dict | None = None, settings: Settings | None = None
) -> EvidenceBundle:
    settings = settings or get_settings()
    t0 = time.monotonic()
    started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    transport = _CountingTransport(settings.investigation_max_requests)
    with geosearch_client(settings, transport) as g, socrata_client(settings, transport) as s:
        resolution = resolve_address(address, g, s)
    if selected:
        resolution = select_option(resolution, selected.get("bbl", ""), selected.get("bin"))
    bundle = EvidenceBundle(
        address=address,
        mode="live",
        resolution=resolution,
        started_at=started,
        selected_option=selected,
    )
    bundle.external_requests = transport.count
    bundle.elapsed_seconds = round(time.monotonic() - t0, 3)
    return bundle


def gather_live(bundle: EvidenceBundle, *, settings: Settings | None = None) -> None:
    """Live retrieval with a wall-clock and request budget checked between sources."""
    settings = settings or get_settings()
    resolution = bundle.resolution
    if resolution.outcome is not Outcome.VERIFIED:
        return
    budget = settings.investigation_max_seconds
    t0 = time.monotonic()
    transport = _CountingTransport(
        max(0, settings.investigation_max_requests - bundle.external_requests)
    )
    with socrata_client(settings, transport) as s:
        steps = [
            ("hpd_buildings", lambda: retrieve_hpd_buildings(resolution, s)),
            ("hpd_violations", lambda: retrieve_hpd_violations(resolution, s)),
            ("hpd_complaints", lambda: retrieve_hpd_complaints(resolution, s)),
            (
                "hpd_registrations",
                lambda: retrieve_hpd_registrations(resolution, s, buildings=bundle.hpd_buildings),
            ),
            (
                "dob_complaints",
                lambda: retrieve_dob_complaints(
                    resolution, s, hpd_buildings=bundle.hpd_buildings, check_upstream_recency=True
                ),
            ),
            (
                "ecb_violations",
                lambda: retrieve_ecb_violations(resolution, s, hpd_buildings=bundle.hpd_buildings),
            ),
            ("sr311", lambda: retrieve_service_requests(resolution, s)),
        ]
        for name, fn in steps:
            if time.monotonic() - t0 > budget:
                bundle.errors.append(f"{name}: skipped, investigation time budget reached")
                continue
            try:
                setattr(bundle, name, fn())
            except Exception as exc:  # one failing adapter must not sink the report
                logger.warning("source %s failed: %s", name, type(exc).__name__)
                bundle.errors.append(f"{name}: {type(exc).__name__}")
    bundle.external_requests += transport.count
    bundle.elapsed_seconds = round(bundle.elapsed_seconds + time.monotonic() - t0, 3)


def resolve_stage(address: str, *, mode: str, selected: dict | None = None) -> EvidenceBundle:
    if mode == "live":
        return resolve_live(address, selected=selected)
    return resolve_demo(address, selected=selected)


def gather_stage(bundle: EvidenceBundle) -> None:
    if bundle.mode == "live":
        gather_live(bundle)
    else:
        gather_demo(bundle)


def collect(address: str, *, mode: str, selected: dict | None = None) -> EvidenceBundle:
    bundle = resolve_stage(address, mode=mode, selected=selected)
    gather_stage(bundle)
    return bundle
