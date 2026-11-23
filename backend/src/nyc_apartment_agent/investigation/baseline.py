"""Deterministic, fixed-order, cited building report (Phase 3 Step 0). No LLM.

Every number in a statement is computed here from typed Phase 2 records, and every
count-style finding carries a FactCheck that verify.py re-computes from the evidence list.
Wording rules (from the accepted Phase 2 decisions):
- HPD violations are official findings; HPD/DOB complaints and 311 requests are reports;
  ECB summonses are charges. They are never merged into one "problem" count.
- An order record (HPD Class I, DOB vacate/stop-work dispositions) is a historical event.
  It is never described as currently in effect.
- Records on another building of the same tax lot are listed separately and never counted
  as the requested building's; tax-lot results never make building-specific claims.
- A missing or partial source is shown as such, never as "no problems".
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Iterable
from email.utils import parsedate_to_datetime

from ..records.admin_models import REGISTRATION_STATUS_NOTE, RequestedBuildingStatus
from ..records.complaint_models import ComplaintGroup
from ..records.dob_models import NormalizedDobComplaint
from ..records.ecb_models import CorrectionStatus, NormalizedEcbViolation, Severity
from ..records.models import NormalizedViolation, RetrievalStatus
from ..records.sr311_models import NormalizedServiceRequest
from ..resolver import Outcome
from ..resolver.models import ResolutionLevel
from .collect import DEMO_ADDRESSES, EvidenceBundle
from .schema import (
    SECTION_TITLES,
    BuildingOverview,
    Category,
    EvidenceItem,
    FactCheck,
    Finding,
    Importance,
    OfficialLink,
    ResearchReport,
    ResolutionOption,
    ResolutionView,
    RunInfo,
    Scope,
    Section,
    SectionId,
    SourceStatus,
    Stat,
    TimelineEvent,
)

OPEN_DATA = "https://data.cityofnewyork.us"

DATASETS = {
    "hpd_violations": ("HPD", "wvxf-dwi5", "Housing Maintenance Code Violations"),
    "hpd_complaints": ("HPD", "ygpa-z7cr", "Housing Maintenance Code Complaints and Problems"),
    "hpd_buildings": ("HPD", "kj4p-ruqc", "Buildings Subject to HPD Jurisdiction"),
    "hpd_registrations": ("HPD", "tesw-yqqr", "Multiple Dwelling Registrations"),
    "dob_complaints": ("DOB", "eabe-havv", "DOB Complaints Received"),
    "ecb_violations": ("DOB / ECB", "6bgk-3dad", "DOB ECB Violations"),
    "sr311": ("311", "erm2-nwe9", "311 Service Requests from 2010 to Present"),
}

SOURCE_ORDER = list(DATASETS)

STATUS_LABELS = {
    "records_found": "Checked: records found",
    "no_records_found": "Checked: none on file",
    "source_unavailable": "Could not be checked",
    "incomplete_pagination": "Partially retrieved",
    "unsupported_join": "Not applicable at this resolution",
    "not_in_demo": "Not in demo dataset",
    "error": "Failed",
}

HPD_CLASS_LABEL = {
    "A": "Class A (non-hazardous)",
    "B": "Class B (hazardous)",
    "C": "Class C (immediately hazardous)",
    "I": "Class I (information / order)",
}

ORDER_LABEL = {
    "vacate_order": "vacate order",
    "vacant_premises_order": "vacant premises order",
    "relocation_order": "relocation order",
    "alternative_enforcement_program": "Alternative Enforcement Program (AEP) order",
    "underlying_conditions_order": "underlying conditions order",
    "registration_noncompliance": "registration-related order",
    "other": "order or information record",
}

SECTION_INTROS = {
    SectionId.SUMMARY: "The most important points, each linked to the records behind it.",
    SectionId.IDENTITY: (
        "Which building the address was matched to, and how confident that match is. "
        "Records are attached only through identifiers the resolver verified."
    ),
    SectionId.HPD_VIOLATIONS: (
        "Violations are HPD's own findings after an inspection (verified records). 'Open' "
        "means HPD has not recorded the violation as corrected; a condition can be fixed "
        "before the record is closed, and vice versa."
    ),
    SectionId.HPD_COMPLAINTS: (
        "Complaints are what tenants or others reported to HPD (allegations). A complaint is "
        "not proof that a condition existed; HPD's inspection result is shown separately."
    ),
    SectionId.DOB_COMPLAINTS: (
        "Complaints to the Department of Buildings (allegations), matched by building BIN. "
        "DOB's disposition code says what DOB recorded after looking into it."
    ),
    SectionId.ECB_VIOLATIONS: (
        "ECB summonses are charges DOB issued. A summons is not by itself a finding that a "
        "violation occurred; DOB's correction status and OATH's hearing outcome are shown "
        "separately and never combined."
    ),
    SectionId.SR311: (
        "311 requests to agencies other than HPD and DOB since 2020. They are reports, not "
        "findings, and they are matched by tax lot, never to one building."
    ),
    SectionId.REGISTRATION: (
        "What HPD has on file about the building and its registration. An administrative "
        "record is not proof of ownership, management or legal status."
    ),
    SectionId.ORDERS: (
        "Orders (vacate, stop-work, AEP and similar) found in the records. Each is a "
        "historical event on its recorded date. These datasets do not show whether an order "
        "is in effect today."
    ),
    SectionId.OTHER_BUILDINGS: (
        "Records filed under a different building (BIN) on the same tax lot, or that could "
        "not be tied to one building. They are kept apart and never counted as this "
        "building's."
    ),
    SectionId.DATA_GAPS: (
        "What could not be checked, what was only partly retrieved, and known limits of the "
        "data. Missing data is never reported as 'no problems'."
    ),
}


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def _plural(n: int | float, word: str, plural: str | None = None) -> str:
    n = int(n)
    return f"{n:,} {word if n == 1 else (plural or word + 's')}"


def _span(years: list[int], lead: str = "between") -> str:
    if not years:
        return ""
    if years[0] == years[-1]:
        return f" in {years[0]}"
    return (
        f" {lead} {years[0]} and {years[-1]}"
        if lead == "between"
        else (f" from {years[0]} to {years[-1]}")
    )


def _a(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _known(x: int | None) -> str:
    return "an unknown number of" if x is None else str(x)


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _header_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        try:
            return dt.date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _recent_tags(d: dt.date | None, as_of: dt.date) -> list[str]:
    if d is None or d > as_of:
        return []
    days = (as_of - d).days
    tags = []
    if days <= 365:
        tags.append("recent_12m")
    if days <= 3 * 365:
        tags.append("recent_36m")
    return tags


def _status_tag(is_open: bool | None) -> str:
    return "open" if is_open else ("closed" if is_open is False else "status_unknown")


def _row_url(dataset_id: str, field: str, value: str | None) -> str | None:
    if not value:
        return None
    return f"{OPEN_DATA}/resource/{dataset_id}.json?{field}={value}"


def _dataset_url(dataset_id: str) -> str:
    return f"{OPEN_DATA}/d/{dataset_id}"


def _short(text: str | None, n: int = 110) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class ReportBuilder:
    def __init__(self, bundle: EvidenceBundle, *, now: dt.datetime | None = None) -> None:
        self.b = bundle
        self.res = bundle.resolution
        self.now = now or dt.datetime.now(dt.UTC)
        self.findings: list[Finding] = []
        self.evidence: dict[str, EvidenceItem] = {}
        self.timeline: list[TimelineEvent] = []
        self.sources: list[SourceStatus] = []
        self._n = 0
        verified = self.res.outcome is Outcome.VERIFIED and self.res.building is not None
        self.building_level = verified and self.res.resolution_level is ResolutionLevel.BUILDING
        self.primary_attr = "this_building" if self.building_level else "lot_level"
        self.primary_scope = Scope.THIS_BUILDING if self.building_level else Scope.LOT_LEVEL
        self.where_phrase = "this building" if self.building_level else "this tax lot"

    # ---- helpers ------------------------------------------------------------------------

    def as_of(self, source: str) -> dt.date:
        rec = self.b.recorded_at.get(source)
        if rec:
            return dt.date.fromisoformat(rec[:10])
        return self.now.date()

    def add_finding(self, **kw) -> Finding:
        self._n += 1
        f = Finding(id=f"F{self._n:02d}", **kw)
        self.findings.append(f)
        return f

    def add_evidence(
        self,
        item: EvidenceItem,
        *,
        timeline_label: str | None = None,
        category: Category | None = None,
        notable: bool = False,
    ) -> None:
        self.evidence[item.id] = item
        if item.date and timeline_label and category:
            self.timeline.append(
                TimelineEvent(
                    date=item.date,
                    year=int(item.date[:4]),
                    source=item.source,
                    agency=item.agency,
                    category=category,
                    attribution=item.attribution,
                    label=timeline_label,
                    evidence_id=item.id,
                    notable=notable,
                )
            )

    def select(
        self,
        source: str,
        attribution: Iterable[str] | None = None,
        tags_all: Iterable[str] = (),
        tags_any: Iterable[str] = (),
    ) -> list[EvidenceItem]:
        attrs = set(attribution) if attribution is not None else None
        tags_all, tags_any = set(tags_all), set(tags_any)
        out = []
        for e in self.evidence.values():
            if e.source != source:
                continue
            if attrs is not None and e.attribution not in attrs:
                continue
            tags = set(e.tags)
            if not tags_all <= tags:
                continue
            if tags_any and not (tags & tags_any):
                continue
            out.append(e)
        return out

    def count_fact(self, source: str, attribution: list[str], tags_all=(), tags_any=()):
        items = self.select(source, attribution, tags_all, tags_any)
        fact = FactCheck(
            metric="count",
            value=len(items),
            where={
                "source": source,
                "attribution": attribution,
                "tags_all": list(tags_all),
                "tags_any": list(tags_any),
            },
        )
        return items, fact

    # ---- sources -----------------------------------------------------------------------

    def source_status(self, name: str, result, *, note: str | None = None) -> SourceStatus:
        agency, dsid, dsname = DATASETS[name]
        if result is None:
            if self.b.mode == "demo" and not self.b.in_demo_dataset:
                status, detail = "not_in_demo", "This address has no recorded data in the demo."
            elif any(e.startswith(name) for e in self.b.errors):
                status = "error"
                detail = next(e for e in self.b.errors if e.startswith(name))
            else:
                status, detail = "unsupported_join", "Not retrieved for this resolution."
            st = SourceStatus(
                source=name,
                agency=agency,
                dataset_id=dsid,
                dataset_name=dsname,
                dataset_url=_dataset_url(dsid),
                status=status,
                status_label=STATUS_LABELS[status],
                status_detail=detail,
                complete=False,
                records=0,
                note=note,
            )
            self.sources.append(st)
            return st
        status = result.status.value
        freshness = getattr(result, "freshness", None)
        data_as_of = result.provenance.data_as_of
        stale = None
        if freshness is not None:
            data_as_of = freshness.data_updated_at or data_as_of
            stale = freshness.stale
        st = SourceStatus(
            source=name,
            agency=agency,
            dataset_id=dsid,
            dataset_name=dsname,
            dataset_url=_dataset_url(dsid),
            status=status,
            status_label=STATUS_LABELS.get(status, status),
            status_detail=result.status_detail,
            complete=result.status
            in (RetrievalStatus.RECORDS_FOUND, RetrievalStatus.NO_RECORDS_FOUND),
            records=len(getattr(result, "complaints", None) or result.records),
            data_as_of=data_as_of,
            retrieved_at=self.b.recorded_at.get(name)
            or (result.provenance.pages[0].fetched_at if result.provenance.pages else None),
            stale=stale,
            note=note,
        )
        self.sources.append(st)
        return st

    # ---- evidence builders -------------------------------------------------------------

    def ev_violation(self, r: NormalizedViolation, as_of: dt.date) -> None:
        d = r.dates.get("inspectiondate")
        date = d.value if d else None
        if date is None and r.dates.get("novissueddate"):
            date = r.dates["novissueddate"].value
        tags = [f"class:{r.violation_class}", _status_tag(r.is_open), *_recent_tags(date, as_of)]
        if r.rent_impairing:
            tags.append("rent_impairing")
        if r.is_informational:
            tags.append("order")
            if r.order_category:
                tags.append(f"order:{r.order_category.value}")
        if r.potentially_consequential:
            tags.append("consequential")
        cls = HPD_CLASS_LABEL.get(r.violation_class or "", f"Class {r.violation_class}")
        if r.is_informational and r.order_category:
            title = f"HPD {ORDER_LABEL.get(r.order_category.value, 'order')} record"
        else:
            title = f"HPD {cls} violation"
        nov = r.dates.get("novissueddate")
        csd = r.dates.get("currentstatusdate")
        item = EvidenceItem(
            id=f"hpd_violation:{r.violation_id}",
            source="hpd_violations",
            agency="HPD",
            dataset_id=r.dataset_id,
            dataset_name=DATASETS["hpd_violations"][2],
            record_id=r.violation_id,
            evidence_kind=r.evidence_kind.value,
            attribution=r.attribution.value,
            title=title,
            date=_iso(date),
            date_label="Inspection date",
            status=f"{r.violation_status_raw or 'unknown'} · {r.current_status_raw or ''}".strip(
                " ·"
            ),
            is_open=r.is_open,
            tags=tags,
            details={
                "Class": r.violation_class,
                "Description (unit detail redacted)": r.description,
                "Current status": r.current_status_raw,
                "Current status date": _iso(csd.value) if csd else None,
                "NOV issued": _iso(nov.value) if nov else None,
                "NOV type": r.nov_type,
                "Rent impairing": r.rent_impairing,
                "Where in building": r.location_scope.value.replace("_", " "),
                "Order interpretation": r.order_interpretation,
                "Record BIN": r.source_bin,
                "HPD building ID": r.building_id,
            },
            flags=[f.value for f in r.quality_flags],
            source_url=_row_url(r.dataset_id, "violationid", r.violation_id),
            dataset_url=_dataset_url(r.dataset_id),
            data_as_of=r.data_as_of,
            fetched_at=r.fetched_at,
        )
        notable = r.violation_class == "C" or r.is_informational
        label = (
            f"{title}"
            if r.is_informational
            else f"HPD Class {r.violation_class} violation: {_short(r.description, 80)}"
        )
        self.add_evidence(
            item,
            timeline_label=label,
            category=Category.HISTORICAL_EVENT if r.is_informational else Category.VERIFIED_RECORD,
            notable=notable,
        )

    def ev_complaint(self, g: ComplaintGroup, as_of: dt.date, data_as_of: str | None) -> None:
        date = g.received_date.value
        cats = sorted(set(g.major_categories))
        tags = [_status_tag(g.is_open), *_recent_tags(date, as_of)]
        tags += [f"category:{c}" for c in cats]
        if any("EMERGENCY" in (t or "") for t in g.types):
            tags.append("emergency")
        outcome_text = ", ".join(
            f"{k.replace('_', ' ')} ({v})" for k, v in sorted(g.outcomes.items())
        )
        item = EvidenceItem(
            id=f"hpd_complaint:{g.complaint_id}",
            source="hpd_complaints",
            agency="HPD",
            dataset_id="ygpa-z7cr",
            dataset_name=DATASETS["hpd_complaints"][2],
            record_id=g.complaint_id,
            evidence_kind=g.evidence_kind.value,
            attribution=g.attribution.value,
            title=f"HPD complaint: {', '.join(c.title() for c in cats) or 'category not stated'}",
            date=_iso(date),
            date_label="Received",
            status=g.complaint_status_raw,
            is_open=g.is_open,
            tags=tags,
            details={
                "Problem categories": ", ".join(cats),
                "Complaint type": ", ".join(sorted(set(t for t in g.types if t))),
                "Problems reported": g.problem_count,
                "HPD outcome per problem": outcome_text,
                "Duplicate problem reports": g.duplicate_problem_count,
            },
            flags=[f.value for f in g.quality_flags],
            source_url=_row_url("ygpa-z7cr", "complaint_id", g.complaint_id),
            dataset_url=_dataset_url("ygpa-z7cr"),
            data_as_of=data_as_of,
        )
        self.add_evidence(
            item,
            timeline_label=f"HPD complaint: {', '.join(c.title() for c in cats) or 'unspecified'}",
            category=Category.COMPLAINT_ALLEGATION,
        )

    def ev_dob(self, r: NormalizedDobComplaint, as_of: dt.date) -> None:
        date = r.received_date.value
        tags = [
            "open"
            if r.complaint_status.value == "active"
            else _status_tag(False if r.complaint_status.value == "closed" else None),
            f"outcome:{r.outcome.value}",
            *_recent_tags(date, as_of),
        ]
        if r.order_event:
            tags += ["order_event", f"order:{r.order_event.kind.value}"]
        if r.complaint_category_description:
            tags.append(f"category:{r.complaint_category_description}")
        item = EvidenceItem(
            id=f"dob_complaint:{r.complaint_number}",
            source="dob_complaints",
            agency="DOB",
            dataset_id=r.dataset_id,
            dataset_name=DATASETS["dob_complaints"][2],
            record_id=r.complaint_number,
            evidence_kind=r.evidence_kind.value,
            attribution=r.attribution.value,
            title=f"DOB complaint: {r.complaint_category_description or 'category ' + str(r.complaint_category)}",  # noqa: E501
            date=_iso(date),
            date_label="Received",
            status=r.status_raw,
            is_open=r.complaint_status.value == "active"
            if r.complaint_status.value != "unknown"
            else None,
            tags=tags,
            details={
                "Category": r.complaint_category_description,
                "Category priority": r.complaint_category_priority,
                "Disposition code": r.disposition_code,
                "Disposition (DOB's text)": r.disposition_description,
                "Disposition date": _iso(r.disposition_date.value),
                "Inspection date": _iso(r.inspection_date.value),
                "Record BIN": r.source_bin,
                "DOB recorded address": r.recorded_address,
                "Order note": r.order_event.statement if r.order_event else None,
            },
            flags=[f.value for f in r.quality_flags],
            source_url=_row_url(r.dataset_id, "complaint_number", r.complaint_number),
            dataset_url=_dataset_url(r.dataset_id),
            data_as_of=r.data_as_of,
            fetched_at=r.fetched_at,
        )
        self.add_evidence(
            item,
            timeline_label=f"DOB complaint: {r.complaint_category_description or 'unspecified'}",
            category=Category.COMPLAINT_ALLEGATION,
        )
        if (
            r.order_event
            and r.order_event.disposition_date
            and (r.order_event.disposition_date.value)
        ):
            d = r.order_event.disposition_date.value
            self.timeline.append(
                TimelineEvent(
                    date=d.isoformat(),
                    year=d.year,
                    source="dob_complaints",
                    agency="DOB",
                    category=Category.HISTORICAL_EVENT,
                    attribution=r.attribution.value,
                    label=f"DOB recorded: {r.order_event.dob_text} (historical record)",
                    evidence_id=item.id,
                    notable=True,
                )
            )

    def ev_ecb(self, r: NormalizedEcbViolation, as_of: dt.date) -> None:
        date = r.issue_date.value
        rid = r.ecb_violation_number or r.isn_dob_bis_extract
        tags = [
            f"correction:{r.correction_status.value}",
            f"adjudication:{r.adjudication.value}",
            f"severity:{r.severity.value}",
            *_recent_tags(date, as_of),
        ]
        if r.severity in (Severity.HAZARDOUS, Severity.CLASS_1):
            tags.append("hazardous_or_class1")
        is_open = (
            True
            if r.correction_status is CorrectionStatus.OPEN_WITH_DOB
            else False
            if r.correction_status is CorrectionStatus.RESOLVED_WITH_DOB
            else None
        )
        tags.append(_status_tag(is_open))
        amounts = {}
        for k in ("penalty_imposed", "amount_paid", "balance_due"):
            v = getattr(r, k)
            if v is not None:
                amounts[k] = float(v)
        laws = "; ".join(
            f"{i.code}: {_short(i.law_description, 90)}" if i.law_description else (i.code or "")
            for i in r.infractions
            if i.code
        )
        item = EvidenceItem(
            id=f"ecb:{rid}",
            source="ecb_violations",
            agency="DOB / ECB",
            dataset_id=r.dataset_id,
            dataset_name=DATASETS["ecb_violations"][2],
            record_id=rid,
            evidence_kind=r.evidence_kind.value,
            attribution=r.attribution.value,
            title=f"ECB summons: {r.violation_type or 'type not stated'}",
            date=_iso(date),
            date_label="Issued",
            status=f"DOB: {r.correction_status_raw or 'unknown'} · OATH: "
            f"{r.hearing_status_raw or 'unknown'}",
            is_open=is_open,
            tags=tags,
            details={
                "Violation type": r.violation_type,
                "Severity (as recorded)": r.severity_raw,
                "DOB correction status": r.correction_status_raw,
                "Hearing outcome (grouped)": r.adjudication.value.replace("_", " "),
                "Hearing note": r.adjudication_note,
                "Hearing date": _iso(r.hearing_date.value),
                "Infraction codes": laws or None,
                "Penalty imposed": float(r.penalty_imposed)
                if r.penalty_imposed is not None
                else None,
                "Balance due (as recorded)": float(r.balance_due)
                if r.balance_due is not None
                else None,
                "Record BIN": r.source_bin,
                "Record lot": r.source_lot,
            },
            amounts=amounts,
            flags=[f.value for f in r.quality_flags],
            source_url=_row_url(
                r.dataset_id,
                "ecb_violation_number" if r.ecb_violation_number else "isn_dob_bis_extract",
                rid,
            ),
            dataset_url=_dataset_url(r.dataset_id),
            data_as_of=r.data_as_of,
            fetched_at=r.fetched_at,
        )
        self.add_evidence(
            item,
            timeline_label=f"ECB summons issued: {r.violation_type or 'unspecified'}",
            category=Category.ENFORCEMENT_ACTION,
            notable="hazardous_or_class1" in tags,
        )

    def ev_311(self, r: NormalizedServiceRequest, as_of: dt.date) -> None:
        date = r.received_date.value
        status_open = (
            True
            if r.request_status.value == "open"
            else False
            if r.request_status.value in ("closed", "canceled")
            else None
        )
        tags = [
            f"agency:{r.agency}",
            f"type:{r.complaint_type}",
            _status_tag(status_open),
            *_recent_tags(date, as_of),
        ]
        item = EvidenceItem(
            id=f"sr311:{r.request_ref}",
            source="sr311",
            agency="311",
            dataset_id=r.dataset_id,
            dataset_name=DATASETS["sr311"][2],
            record_id=r.request_ref,
            evidence_kind=r.evidence_kind.value,
            attribution=r.attribution.value,
            title=f"311: {r.complaint_type or 'type not stated'} ({r.agency or 'agency withheld'})",
            date=_iso(date),
            date_label="Received",
            status=r.status_raw,
            is_open=status_open,
            tags=tags,
            details={
                "Agency": r.agency,
                "Complaint type": r.complaint_type,
                "Descriptor": r.descriptor,
                "Location type": r.location_type,
                "Agency resolution (allowlisted text)": r.resolution_description,
                "Closed": _iso(r.closed_date.value),
                "Note": "The 311 request number is withheld for privacy; this is a one-way token.",
            },
            flags=[f.value for f in r.quality_flags],
            source_url=None,
            dataset_url=_dataset_url(r.dataset_id),
            data_as_of=r.data_as_of,
            fetched_at=r.fetched_at,
        )
        self.add_evidence(
            item,
            timeline_label=f"311: {r.complaint_type or 'request'} ({r.agency or '?'})",
            category=Category.COMPLAINT_ALLEGATION,
        )

    # ---- sections ----------------------------------------------------------------------

    def identity(self) -> None:
        res = self.res
        if res.outcome is not Outcome.VERIFIED or res.building is None:
            return
        b = res.building
        others = [x for x in (b.footprint_bins_on_lot or []) if x != b.bin]
        if self.building_level:
            stmt = (
                f"The address matched tax lot (BBL) {b.bbl} and building BIN {b.bin}. The BIN "
                "was cross-verified: the city's address database (PAD, via GeoSearch) and "
                "Building Footprints place it on the same lot."
            )
            if self.b.selected_option:
                stmt += " You picked this building from the resolver's options."
            self.add_finding(
                section=SectionId.IDENTITY,
                category=Category.IDENTITY,
                scope=Scope.THIS_BUILDING,
                importance=Importance.INFO,
                title="Building verified",
                statement=stmt,
            )
        else:
            self.add_finding(
                section=SectionId.IDENTITY,
                category=Category.UNCERTAIN,
                scope=Scope.LOT_LEVEL,
                importance=Importance.HIGH,
                title="Only the tax lot could be verified",
                statement=(
                    f"The address matched tax lot (BBL) {b.bbl}, but no building (BIN) could "
                    f"be verified for it ({b.bin_evidence.value.replace('_', ' ')}). Records "
                    "below describe the whole lot and may concern other structures on it. "
                    "No building-specific claims are made, and DOB complaints (searched by "
                    "BIN) are not attached."
                ),
            )
        if others and not self.building_level:
            self.add_finding(
                section=SectionId.IDENTITY,
                category=Category.ADMINISTRATIVE_RECORD,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.INFO,
                title="Building Footprints context",
                statement=(
                    f"Building Footprints lists BIN {', '.join(others)} on this tax lot. The "
                    "resolver did not adopt it as this address's building, so it is shown as "
                    "context only."
                ),
            )
        elif others:
            self.add_finding(
                section=SectionId.IDENTITY,
                category=Category.ADMINISTRATIVE_RECORD,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.MEDIUM,
                title="Other buildings share this tax lot",
                statement=(
                    f"Building Footprints lists {_plural(len(others), 'other building')} on "
                    f"this tax lot (BIN {', '.join(others)}). Records filed under those BINs "
                    "are shown in 'Records for other buildings on this lot' and are never "
                    "counted as this building's."
                ),
            )

    def hpd_violations(self) -> None:
        r = self.b.hpd_violations
        self.source_status("hpd_violations", r)
        if r is None or r.status is RetrievalStatus.UNSUPPORTED_JOIN:
            return
        as_of = self.as_of("hpd_violations")
        for v in r.records:
            self.ev_violation(v, as_of)
        attr = [self.primary_attr]
        if r.status is RetrievalStatus.NO_RECORDS_FOUND:
            self.add_finding(
                section=SectionId.HPD_VIOLATIONS,
                category=Category.VERIFIED_RECORD,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="No HPD violations on file",
                statement=(
                    f"HPD's violation data reports 0 violations for {self.where_phrase} "
                    f"(dataset version {r.provenance.data_as_of or 'unknown'}). Buildings "
                    "outside HPD's jurisdiction (for example, many commercial buildings) "
                    "normally have none."
                ),
                fact=FactCheck(
                    metric="count",
                    value=0,
                    where={"source": "hpd_violations", "attribution": attr},
                ),
            )
            return
        if r.status in (RetrievalStatus.SOURCE_UNAVAILABLE,):
            return
        partial = r.status is RetrievalStatus.INCOMPLETE_PAGINATION
        caveat = "Partial retrieval: counts are lower bounds." if partial else None
        all_items, fact = self.count_fact(
            "hpd_violations", attr, tags_any=("class:A", "class:B", "class:C")
        )  # noqa: E501
        if all_items:
            years = sorted(int(e.date[:4]) for e in all_items if e.date)
            span = _span(years)
            self.add_finding(
                section=SectionId.HPD_VIOLATIONS,
                category=Category.VERIFIED_RECORD,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="HPD violations on record",
                statement=(
                    f"HPD recorded {_plural(len(all_items), 'Class A/B/C violation')} for "
                    f"{self.where_phrase}{span}, open and closed."
                ),
                evidence_ids=[e.id for e in all_items],
                fact=fact,
                caveat=caveat,
            )
        open_items, fact = self.count_fact(
            "hpd_violations", attr, tags_all=("open",), tags_any=("class:A", "class:B", "class:C")
        )
        if open_items:
            by = Counter(e.details.get("Class") for e in open_items)
            parts = [f"{by[c]} {HPD_CLASS_LABEL[c]}" for c in ("C", "B", "A") if by.get(c)]
            f = self.add_finding(
                section=SectionId.HPD_VIOLATIONS,
                category=Category.VERIFIED_RECORD,
                scope=self.primary_scope,
                importance=Importance.HIGH if by.get("C") else Importance.MEDIUM,
                title="Open HPD violations",
                statement=(
                    f"{_plural(len(open_items), 'violation')} for {self.where_phrase} "
                    f"{'is' if len(open_items) == 1 else 'are'} open in HPD's data: "
                    f"{', '.join(parts)}."
                ),
                evidence_ids=[e.id for e in open_items],
                fact=fact,
                caveat=(
                    "Open means HPD has not recorded the violation as corrected. It does not "
                    "show when or whether the condition was fixed."
                ),
            )
            self.stats.append(
                Stat(
                    label="Open HPD violations",
                    value=f"{len(open_items):,}",
                    tone="attention",
                    finding_id=f.id,
                    hint=f"{by.get('C', 0)} Class C (immediately hazardous)",
                )
            )
        else:
            self.stats.append(
                Stat(
                    label="Open HPD violations",
                    value="0",
                    tone="good",
                    hint="none open in HPD's data",
                )
            )
        c_open, fact = self.count_fact("hpd_violations", attr, tags_all=("open", "class:C"))
        if c_open:
            self.add_finding(
                section=SectionId.HPD_VIOLATIONS,
                category=Category.VERIFIED_RECORD,
                scope=self.primary_scope,
                importance=Importance.HIGH,
                title="Open immediately hazardous (Class C) violations",
                statement=(
                    f"{_plural(len(c_open), 'open violation')} for {self.where_phrase} "
                    f"{'is' if len(c_open) == 1 else 'are'} Class C, HPD's immediately "
                    "hazardous class."
                ),
                evidence_ids=[e.id for e in c_open],
                fact=fact,
            )
        rent, fact = self.count_fact("hpd_violations", attr, tags_all=("open", "rent_impairing"))
        if rent:
            self.add_finding(
                section=SectionId.HPD_VIOLATIONS,
                category=Category.VERIFIED_RECORD,
                scope=self.primary_scope,
                importance=Importance.MEDIUM,
                title="Open rent-impairing violations",
                statement=(
                    f"HPD marks {_plural(len(rent), 'open violation')} for {self.where_phrase}"
                    " as rent impairing."
                ),
                evidence_ids=[e.id for e in rent],
                fact=fact,
            )
        recent, fact = self.count_fact(
            "hpd_violations",
            attr,
            tags_all=("recent_12m",),
            tags_any=("class:A", "class:B", "class:C"),
        )
        self.add_finding(
            section=SectionId.HPD_VIOLATIONS,
            category=Category.VERIFIED_RECORD,
            scope=self.primary_scope,
            importance=Importance.MEDIUM if recent else Importance.INFO,
            title="Violations from the last 12 months",
            statement=(
                f"{_plural(len(recent), 'violation')} for {self.where_phrase} came from "
                f"inspections in the 12 months before {as_of.isoformat()} (the data date)."
            ),
            evidence_ids=[e.id for e in recent],
            fact=fact,
        )
        # Orders (Class I) are historical events, never "current".
        orders = self.select("hpd_violations", attr, tags_all=("order",))
        for e in sorted(orders, key=lambda x: x.date or ""):
            cat = next((t.split(":", 1)[1] for t in e.tags if t.startswith("order:")), "other")
            self.add_finding(
                section=SectionId.ORDERS,
                category=Category.HISTORICAL_EVENT,
                scope=self.primary_scope,
                importance=(
                    Importance.HIGH
                    if "consequential" in e.tags and e.is_open
                    else Importance.MEDIUM
                    if "consequential" in e.tags or e.is_open
                    else Importance.INFO
                ),
                title=f"HPD {ORDER_LABEL.get(cat, 'order')} on record",
                statement=(
                    f"HPD's data includes {_a(ORDER_LABEL.get(cat, 'order'))} record for "
                    f"{self.where_phrase} dated {e.date or 'with no readable date'} "
                    f"(record status: {e.status}). " + (e.details.get("Order interpretation") or "")
                ).strip(),
                evidence_ids=[e.id],
                caveat="A record that an order was issued is not proof that it is in effect today.",
            )
        self.other_bucket("hpd_violations", "HPD violation")

    def other_bucket(self, source: str, noun: str, plural: str | None = None) -> None:
        """Records on another building, or on the lot without a usable BIN."""
        if not self.building_level:
            return
        for attr, scope, phrase in (
            (
                "other_building_on_lot",
                Scope.OTHER_BUILDING_ON_LOT,
                "filed under a different building (BIN) on the same tax lot",
            ),
            (
                "unattributed_on_lot",
                Scope.UNATTRIBUTED_ON_LOT,
                "on this tax lot but could not be tied to one building",
            ),
            (
                "mixed",
                Scope.MIXED,
                "mixing problems tied to this building and to other buildings or the lot",
            ),
        ):
            items, fact = self.count_fact(source, [attr])
            if not items:
                continue
            bins = sorted(
                {str(e.details.get("Record BIN")) for e in items if e.details.get("Record BIN")}
            )
            bin_txt = f" (record BIN {', '.join(bins)})" if bins and attr != "mixed" else ""
            self.add_finding(
                section=SectionId.OTHER_BUILDINGS,
                category=Category.UNCERTAIN
                if attr != "other_building_on_lot"
                else Category.ADMINISTRATIVE_RECORD,
                scope=scope,
                importance=Importance.MEDIUM,
                title=f"{(plural or noun + 's')[0].upper()}{(plural or noun + 's')[1:]} not "
                "counted as this building's",
                statement=(
                    f"{_plural(len(items), noun, plural)} "
                    f"{'is' if len(items) == 1 else 'are'} {phrase}{bin_txt}. "
                    f"{'It is' if len(items) == 1 else 'They are'} not counted in this "
                    "building's totals."
                ),
                evidence_ids=[e.id for e in items],
                fact=fact,
            )

    def hpd_complaints(self) -> None:
        r = self.b.hpd_complaints
        self.source_status("hpd_complaints", r)
        if r is None or r.status in (
            RetrievalStatus.UNSUPPORTED_JOIN,
            RetrievalStatus.SOURCE_UNAVAILABLE,
        ):
            return
        as_of = self.as_of("hpd_complaints")
        for g in r.complaints:
            self.ev_complaint(g, as_of, r.provenance.data_as_of)
        attr = [self.primary_attr]
        items, fact = self.count_fact("hpd_complaints", attr)
        if not items:
            self.add_finding(
                section=SectionId.HPD_COMPLAINTS,
                category=Category.COMPLAINT_ALLEGATION,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="No HPD complaints on file",
                statement=f"HPD's complaint data has no complaints for {self.where_phrase}.",
                fact=fact,
            )
            self.other_bucket("hpd_complaints", "HPD complaint")
            return
        years = sorted(int(e.date[:4]) for e in items if e.date)
        cats = Counter(
            t.split(":", 1)[1] for e in items for t in e.tags if t.startswith("category:")
        )
        top = ", ".join(f"{c.title()} ({n})" for c, n in cats.most_common(4))
        self.add_finding(
            section=SectionId.HPD_COMPLAINTS,
            category=Category.COMPLAINT_ALLEGATION,
            scope=self.primary_scope,
            importance=Importance.INFO,
            title="Complaints to HPD on record",
            statement=(
                f"HPD recorded {_plural(len(items), 'complaint')} about {self.where_phrase}"
                + _span(years, "from")
                + (f". Most common problem categories: {top}." if top else ".")
            ),
            evidence_ids=[e.id for e in items],
            fact=fact,
            caveat="A complaint can list several problems, so category counts overlap.",
        )
        recent, fact = self.count_fact("hpd_complaints", attr, tags_all=("recent_12m",))
        f = self.add_finding(
            section=SectionId.HPD_COMPLAINTS,
            category=Category.COMPLAINT_ALLEGATION,
            scope=self.primary_scope,
            importance=Importance.MEDIUM if recent else Importance.INFO,
            title="Complaints in the last 12 months",
            statement=(
                f"{_plural(len(recent), 'complaint')} about {self.where_phrase} "
                f"{'was' if len(recent) == 1 else 'were'} received in the 12 months before "
                f"{as_of.isoformat()} (the data date)."
            ),
            evidence_ids=[e.id for e in recent],
            fact=fact,
        )
        self.stats.append(
            Stat(
                label="HPD complaints, last 12 months",
                value=f"{len(recent):,}",
                tone="attention" if len(recent) >= 10 else "neutral",
                finding_id=f.id,
                hint=f"{len(items):,} on record in total",
            )
        )
        heat, fact = self.count_fact(
            "hpd_complaints",
            attr,
            tags_all=("recent_36m",),
            tags_any=("category:HEAT/HOT WATER", "category:HEATING"),
        )
        if heat:
            self.add_finding(
                section=SectionId.HPD_COMPLAINTS,
                category=Category.COMPLAINT_ALLEGATION,
                scope=self.primary_scope,
                importance=Importance.MEDIUM,
                title="Heat and hot water complaints (last 3 years)",
                statement=(
                    f"{_plural(len(heat), 'complaint')} about {self.where_phrase} in the 3 "
                    f"years before {as_of.isoformat()} reported heat or hot water problems."
                ),
                evidence_ids=[e.id for e in heat],
                fact=fact,
            )
        open_items, fact = self.count_fact("hpd_complaints", attr, tags_all=("open",))
        if open_items:
            self.add_finding(
                section=SectionId.HPD_COMPLAINTS,
                category=Category.COMPLAINT_ALLEGATION,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="Complaints still open",
                statement=(
                    f"{_plural(len(open_items), 'complaint')} about {self.where_phrase} "
                    f"{'is' if len(open_items) == 1 else 'are'} still open in HPD's data."
                ),
                evidence_ids=[e.id for e in open_items],
                fact=fact,
            )
        self.other_bucket("hpd_complaints", "HPD complaint")

    def dob_complaints(self) -> None:
        r = self.b.dob_complaints
        note = None
        if r is not None and r.status is RetrievalStatus.UNSUPPORTED_JOIN:
            note = "DOB complaints are matched by building BIN; this address has no verified BIN."
        self.source_status("dob_complaints", r, note=note)
        if r is None:
            return
        if r.status is RetrievalStatus.UNSUPPORTED_JOIN:
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNAVAILABLE,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.MEDIUM,
                title="DOB complaints not attached",
                statement=(
                    "DOB complaints are filed by building (BIN). Because only the tax lot was "
                    "verified, DOB complaints were not attached, rather than guessing which "
                    "building they belong to."
                ),
            )
            return
        if r.status is RetrievalStatus.SOURCE_UNAVAILABLE:
            return
        as_of = self.as_of("dob_complaints")
        for c in r.records:
            self.ev_dob(c, as_of)
        attr = ["this_building"]
        if r.summary is not None and not r.summary.building_specific_claims_allowed:
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.MEDIUM,
                title="DOB building attribution withheld",
                statement=(
                    "DOB complaints were retrieved, but the building's BIN has conflicting lot "
                    "evidence, so none is counted as this building's. "
                    + (r.summary.building_claims_denied_reason or "")
                ).strip(),
            )
        items, fact = self.count_fact("dob_complaints", attr)
        cats = Counter(e.details.get("Category") or "category not stated" for e in items)
        top = ", ".join(f"{c} ({n})" for c, n in cats.most_common(3))
        f = self.add_finding(
            section=SectionId.DOB_COMPLAINTS,
            category=Category.COMPLAINT_ALLEGATION,
            scope=Scope.THIS_BUILDING,
            importance=Importance.INFO,
            title="Complaints to DOB on record",
            statement=(
                f"DOB recorded {_plural(len(items), 'complaint')} filed under this building's "
                f"BIN" + (f". Most common categories: {top}." if top else ".")
            ),
            evidence_ids=[e.id for e in items],
            fact=fact,
        )
        self.stats.append(
            Stat(label="DOB complaints (this building)", value=f"{len(items):,}", finding_id=f.id)
        )
        active, fact = self.count_fact("dob_complaints", attr, tags_all=("open",))
        if active:
            self.add_finding(
                section=SectionId.DOB_COMPLAINTS,
                category=Category.COMPLAINT_ALLEGATION,
                scope=Scope.THIS_BUILDING,
                importance=Importance.MEDIUM,
                title="DOB complaints still active",
                statement=(
                    f"{_plural(len(active), 'DOB complaint')} for this building "
                    f"{'is' if len(active) == 1 else 'are'} listed as ACTIVE."
                ),
                evidence_ids=[e.id for e in active],
                fact=fact,
            )
        served, fact = self.count_fact(
            "dob_complaints",
            attr,
            tags_any=("outcome:violation_served", "outcome:violation_issued"),
        )
        if served:
            self.add_finding(
                section=SectionId.DOB_COMPLAINTS,
                category=Category.VERIFIED_RECORD,
                scope=Scope.THIS_BUILDING,
                importance=Importance.MEDIUM,
                title="DOB complaints that led to a violation",
                statement=(
                    f"For {_plural(len(served), 'DOB complaint')} about this building, DOB's "
                    "disposition says a violation was served or issued."
                ),
                evidence_ids=[e.id for e in served],
                fact=fact,
            )
        for scope_attr in ("this_building", "other_building_on_lot"):
            for e in sorted(
                self.select("dob_complaints", [scope_attr], tags_all=("order_event",)),
                key=lambda x: x.date or "",
            ):
                other = scope_attr == "other_building_on_lot"
                rescinded = any(t.startswith("order:") and "rescinded" in t for t in e.tags)
                self.add_finding(
                    section=SectionId.OTHER_BUILDINGS if other else SectionId.ORDERS,
                    category=Category.HISTORICAL_EVENT,
                    scope=Scope.OTHER_BUILDING_ON_LOT if other else Scope.THIS_BUILDING,
                    importance=Importance.INFO
                    if rescinded
                    else Importance.MEDIUM
                    if other
                    else Importance.HIGH,
                    title=(
                        f"DOB order event on another building (BIN {e.details.get('Record BIN')})"
                        if other
                        else "DOB order event on record"
                    ),
                    statement=(
                        (
                            f"This record is filed under BIN {e.details.get('Record BIN')}, a "
                            "different building on the same tax lot, not the requested "
                            "building. "
                            if other
                            else ""
                        )
                        + str(e.details.get("Order note") or "")
                    ).strip(),
                    evidence_ids=[e.id],
                    caveat="Historical record only. Check DOB BIS or DOB NOW for the current "
                    "status of any order.",
                )
        self.other_bucket("dob_complaints", "DOB complaint")
        if r.bin_lot_conflicts:
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.MEDIUM,
                title="Conflicting building identifiers",
                statement=(
                    f"Sources place BIN {', '.join(r.bin_lot_conflicts)} on different tax "
                    "lots, so DOB complaints under that BIN were not searched or attributed."
                ),
            )

    def ecb(self) -> None:
        r = self.b.ecb_violations
        self.source_status("ecb_violations", r)
        if r is None or r.status in (
            RetrievalStatus.UNSUPPORTED_JOIN,
            RetrievalStatus.SOURCE_UNAVAILABLE,
        ):
            return
        as_of = self.as_of("ecb_violations")
        for v in r.records:
            self.ev_ecb(v, as_of)
        attr = [self.primary_attr]
        items, fact = self.count_fact("ecb_violations", attr)
        if not items:
            self.add_finding(
                section=SectionId.ECB_VIOLATIONS,
                category=Category.ENFORCEMENT_ACTION,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="No ECB summonses",
                statement=f"DOB's ECB data has no summonses for {self.where_phrase}.",
                fact=fact,
            )
        else:
            years = sorted(int(e.date[:4]) for e in items if e.date)
            types = Counter(e.details.get("Violation type") or "not stated" for e in items)
            self.add_finding(
                section=SectionId.ECB_VIOLATIONS,
                category=Category.ENFORCEMENT_ACTION,
                scope=self.primary_scope,
                importance=Importance.INFO,
                title="ECB summonses on record",
                statement=(
                    f"DOB issued {_plural(len(items), 'ECB summons', 'ECB summonses')} for "
                    f"{self.where_phrase}"
                    + _span(years)
                    + ". Types: "
                    + ", ".join(f"{t} ({n})" for t, n in types.most_common(4))
                    + "."
                ),
                evidence_ids=[e.id for e in items],
                fact=fact,
                caveat="A summons is a charge, not a finding that a violation occurred.",
            )
            active, fact = self.count_fact(
                "ecb_violations", attr, tags_all=("correction:open_with_dob",)
            )
            f = self.add_finding(
                section=SectionId.ECB_VIOLATIONS,
                category=Category.ENFORCEMENT_ACTION,
                scope=self.primary_scope,
                importance=Importance.MEDIUM if active else Importance.INFO,
                title="Summonses DOB lists as not yet resolved",
                statement=(
                    f"DOB lists {_plural(len(active), 'summons', 'summonses')} for "
                    f"{self.where_phrase} as ACTIVE, which DOB's data dictionary describes "
                    "as still needing to be addressed with DOB."
                ),
                evidence_ids=[e.id for e in active],
                fact=fact,
            )
            self.stats.append(
                Stat(
                    label="ECB summonses still active with DOB",
                    value=f"{len(active):,}",
                    tone="attention" if active else "neutral",
                    finding_id=f.id,
                    hint=f"{len(items):,} on record",
                )
            )
            upheld, fact = self.count_fact(
                "ecb_violations", attr, tags_all=("adjudication:upheld",)
            )
            if upheld:
                self.add_finding(
                    section=SectionId.ECB_VIOLATIONS,
                    category=Category.ENFORCEMENT_ACTION,
                    scope=self.primary_scope,
                    importance=Importance.INFO,
                    title="Summonses with an IN VIOLATION hearing outcome",
                    statement=(
                        f"OATH's hearing status (as DOB publishes it) is IN VIOLATION for "
                        f"{_plural(len(upheld), 'summons', 'summonses')}. Outcomes by "
                        "stipulation, admission, cure or default are counted separately."
                    ),
                    evidence_ids=[e.id for e in upheld],
                    fact=fact,
                )
            if self.building_level:
                owed = [e for e in items if e.amounts.get("balance_due", 0) > 0]
                if owed:
                    total = sum(e.amounts["balance_due"] for e in owed)
                    self.add_finding(
                        section=SectionId.ECB_VIOLATIONS,
                        category=Category.ENFORCEMENT_ACTION,
                        scope=Scope.THIS_BUILDING,
                        importance=Importance.INFO,
                        title="Recorded ECB balance due",
                        statement=(
                            f"DOB's ECB data records a balance due on "
                            f"{_plural(len(owed), 'summons', 'summonses')} for this building, "
                            f"totalling {_money(total)} as recorded."
                        ),
                        evidence_ids=[e.id for e in owed],
                        fact=FactCheck(
                            metric="sum",
                            field="balance_due",
                            value=total,
                            where={
                                "source": "ecb_violations",
                                "attribution": ["this_building"],
                                "amount_positive": "balance_due",
                            },
                        ),
                        caveat="Amounts as DOB recorded them; not reconciled with OATH or "
                        "payment records.",
                    )
            undocumented, _ = self.count_fact(
                "ecb_violations",
                attr,
                tags_any=("severity:class_1", "severity:class_2", "severity:class_3"),
            )
            if undocumented:
                self.add_finding(
                    section=SectionId.DATA_GAPS,
                    category=Category.UNCERTAIN,
                    scope=self.primary_scope,
                    importance=Importance.INFO,
                    title="Undocumented ECB severity values",
                    statement=(
                        f"{_plural(len(undocumented), 'summons', 'summonses')} "
                        f"{'uses' if len(undocumented) == 1 else 'use'} severity "
                        "values such as 'CLASS - 1' that the ECB data dictionary does not "
                        "define. They are shown as recorded and not ranked."
                    ),
                    evidence_ids=[e.id for e in undocumented],
                )
        self.other_bucket("ecb_violations", "ECB summons", "ECB summonses")

    def sr311(self) -> None:
        r = self.b.sr311
        self.source_status("sr311", r)
        if r is None or r.status in (
            RetrievalStatus.UNSUPPORTED_JOIN,
            RetrievalStatus.SOURCE_UNAVAILABLE,
        ):
            return
        as_of = self.as_of("sr311")
        for s in r.records:
            self.ev_311(s, as_of)
        attr = ["unattributed_on_lot", "lot_level"]
        items, fact = self.count_fact("sr311", attr)
        partial = r.status is RetrievalStatus.INCOMPLETE_PAGINATION
        types = Counter(e.details.get("Complaint type") or "not stated" for e in items)
        f = self.add_finding(
            section=SectionId.SR311,
            category=Category.COMPLAINT_ALLEGATION,
            scope=Scope.UNATTRIBUTED_ON_LOT if self.building_level else Scope.LOT_LEVEL,
            importance=Importance.INFO,
            title="311 requests about this tax lot",
            statement=(
                f"{_plural(len(items), '311 request')} to agencies other than HPD and DOB "
                "were made about this tax lot since 2020"
                + (
                    ". Most common: "
                    + ", ".join(f"{t} ({n})" for t, n in types.most_common(4))
                    + "."
                    if items
                    else "."
                )
            ),
            evidence_ids=[e.id for e in items],
            fact=fact,
            caveat=(
                "Retrieval was not provably complete, so this count is a lower bound."
                if partial
                else "Matched by tax lot, not by building."
            ),
        )
        self.stats.append(
            Stat(
                label="311 requests since 2020 (tax lot)",
                value=f"{len(items):,}" + ("+" if partial else ""),
                finding_id=f.id,
            )
        )
        recent, fact = self.count_fact("sr311", attr, tags_all=("recent_12m",))
        if recent:
            self.add_finding(
                section=SectionId.SR311,
                category=Category.COMPLAINT_ALLEGATION,
                scope=Scope.UNATTRIBUTED_ON_LOT if self.building_level else Scope.LOT_LEVEL,
                importance=Importance.INFO,
                title="311 requests in the last 12 months",
                statement=(
                    f"{_plural(len(recent), '311 request')} about this tax lot "
                    f"{'was' if len(recent) == 1 else 'were'} made in the 12 months before "
                    f"{as_of.isoformat()}."
                ),
                evidence_ids=[e.id for e in recent],
                fact=fact,
            )

    def registration(self) -> BuildingOverview | None:
        bld = self.b.hpd_buildings
        reg = self.b.hpd_registrations
        self.source_status("hpd_buildings", bld)
        self.source_status("hpd_registrations", reg)
        if bld is None:
            return None
        overview = BuildingOverview()
        for rec in bld.records:
            item = EvidenceItem(
                id=f"hpd_building:{rec.building_id}",
                source="hpd_buildings",
                agency="HPD",
                dataset_id=rec.dataset_id,
                dataset_name=DATASETS["hpd_buildings"][2],
                record_id=rec.building_id,
                evidence_kind=rec.evidence_kind.value,
                attribution=rec.attribution.value,
                title=f"HPD building record {rec.building_id}",
                status=f"{rec.lifecycle or '?'} / {rec.record_status or '?'}",
                tags=["current" if rec.is_current else "non_current"],
                details={
                    "Address (HPD)": rec.address,
                    "Record BIN": rec.source_bin,
                    "Legal stories": rec.legal_stories,
                    "Legal Class A units": rec.legal_class_a_units,
                    "Legal Class B units": rec.legal_class_b_units,
                    "DOB building class": rec.dob_building_class,
                    "Management program": rec.management_program,
                    "Registration ID": rec.registration_id,
                },
                flags=[f.value for f in rec.quality_flags],
                source_url=_row_url(rec.dataset_id, "buildingid", rec.building_id),
                dataset_url=_dataset_url(rec.dataset_id),
                data_as_of=rec.data_as_of,
                fetched_at=rec.fetched_at,
            )
            self.add_evidence(item)
        if reg is not None:
            for rr in reg.records:
                item = EvidenceItem(
                    id=f"hpd_registration:{rr.registration_id}:{rr.source_row_id}",
                    source="hpd_registrations",
                    agency="HPD",
                    dataset_id=rr.dataset_id,
                    dataset_name=DATASETS["hpd_registrations"][2],
                    record_id=rr.registration_id,
                    evidence_kind=rr.evidence_kind.value,
                    attribution=rr.attribution.value,
                    title=f"HPD registration {rr.registration_id}",
                    date=_iso(rr.last_registration_date.value),
                    date_label="Last registered",
                    tags=[],
                    details={
                        "HPD building ID": rr.building_id,
                        "Last registration date": _iso(rr.last_registration_date.value),
                        "Registration end date": _iso(rr.registration_end_date.value),
                        "Buildings on this lot with this registration": (
                            rr.buildings_on_lot_with_this_registration
                        ),
                    },
                    flags=[f.value for f in rr.quality_flags],
                    source_url=_row_url(rr.dataset_id, "registrationid", rr.registration_id),
                    dataset_url=_dataset_url(rr.dataset_id),
                    data_as_of=rr.data_as_of,
                    fetched_at=rr.fetched_at,
                )
                self.add_evidence(
                    item,
                    timeline_label="HPD registration filed (last registration date)",
                    category=Category.ADMINISTRATIVE_RECORD,
                )
        s = bld.summary
        overview.buildings_on_lot = s.total_records if s else None
        rb = s.requested_building if s else None
        if rb and rb.status is RequestedBuildingStatus.ONE_CURRENT_RECORD:
            overview.hpd_building_id = rb.building_id
            overview.hpd_record_status = "current"
            overview.legal_stories = rb.legal_stories
            overview.legal_class_a_units = rb.legal_class_a_units
            overview.legal_class_b_units = rb.legal_class_b_units
            overview.dob_building_class = rb.dob_building_class
            overview.management_program = rb.management_program
            ev = f"hpd_building:{rb.building_id}"
            overview.evidence_ids.append(ev)
            self.add_finding(
                section=SectionId.REGISTRATION,
                category=Category.ADMINISTRATIVE_RECORD,
                scope=Scope.THIS_BUILDING,
                importance=Importance.INFO,
                title="HPD building record",
                statement=(
                    f"HPD's building record {rb.building_id} lists "
                    f"{_known(rb.legal_stories)} legal stories and "
                    f"{_known(rb.legal_class_a_units)}"
                    f" Class A (permanent residence) units; DOB building class "
                    f"{rb.dob_building_class or 'not stated'}."
                ),
                evidence_ids=[ev],
            )
        elif rb and rb.status is RequestedBuildingStatus.MULTIPLE_CURRENT_RECORDS:
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.THIS_BUILDING,
                importance=Importance.INFO,
                title="More than one HPD building record",
                statement=(
                    f"HPD has {len(rb.candidate_building_ids)} current building records for "
                    "this BIN; none was chosen."
                ),
                evidence_ids=[
                    f"hpd_building:{x}"
                    for x in rb.candidate_building_ids
                    if f"hpd_building:{x}" in self.evidence
                ],
            )
        elif rb and rb.status in (
            RequestedBuildingStatus.NO_RECORD_FOR_BIN,
            RequestedBuildingStatus.ONLY_NON_CURRENT_RECORDS,
        ):
            self.add_finding(
                section=SectionId.REGISTRATION,
                category=Category.ADMINISTRATIVE_RECORD,
                scope=Scope.THIS_BUILDING,
                importance=Importance.INFO,
                title="No current HPD building record",
                statement=(
                    "HPD has no current building record for this BIN. That usually means the "
                    "building is outside HPD's residential jurisdiction; it is not a finding "
                    "about the building."
                ),
            )
        # Registration status (D10/D12 wording, never "expired" or "non-compliant").
        rs = reg.summary.requested_building if reg is not None and reg.summary else None
        if rs is not None:
            overview.registration_status = rs.status.value
            overview.registration_note = REGISTRATION_STATUS_NOTE[rs.status]
            overview.registration_end_date = (
                _iso(rs.latest_end_date.value) if (rs.latest_end_date) else None
            )
            ids = [e.id for e in self.select("hpd_registrations", ["this_building"])]
            self.add_finding(
                section=SectionId.REGISTRATION,
                category=Category.ADMINISTRATIVE_RECORD,
                scope=Scope.THIS_BUILDING,
                importance=Importance.INFO,
                title="Registration with HPD",
                statement=REGISTRATION_STATUS_NOTE[rs.status]
                + (
                    f" Latest registration end date on file: {overview.registration_end_date}."
                    if overview.registration_end_date
                    else ""
                ),
                evidence_ids=ids,
                caveat="Registration data does not prove who owns or manages the building.",
            )
        if reg is not None and reg.freshness is not None and reg.freshness.data_updated_at:
            upd = reg.freshness.data_updated_at[:10]
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.INFO,
                title="Registration data freshness",
                statement=(
                    f"HPD's registration dataset was last updated on {upd}"
                    + (
                        " and is older than its stated update schedule"
                        if reg.freshness.stale
                        else ""
                    )
                    + ". Registrations filed after that date would not appear."
                ),
            )
        return overview

    def data_gaps(self) -> None:
        for st in self.sources:
            if st.status in ("source_unavailable", "error"):
                self.add_finding(
                    section=SectionId.DATA_GAPS,
                    category=Category.UNAVAILABLE,
                    scope=Scope.NOT_APPLICABLE,
                    importance=Importance.HIGH,
                    title=f"{st.dataset_name} could not be checked",
                    statement=(
                        f"{st.agency} data ({st.dataset_name}) could not be retrieved, so this "
                        "report says nothing about it. This is not evidence that there are no "
                        "records."
                    ),
                )
            elif st.status == "incomplete_pagination":
                self.add_finding(
                    section=SectionId.DATA_GAPS,
                    category=Category.UNCERTAIN,
                    scope=Scope.NOT_APPLICABLE,
                    importance=Importance.MEDIUM,
                    title=f"{st.dataset_name}: partial retrieval",
                    statement=(
                        f"Retrieval of {st.agency} data ({st.dataset_name}) was not provably "
                        f"complete ({_short(st.status_detail, 160)}). Counts from it are lower "
                        "bounds."
                    ),
                )
        if any(e.category is Category.HISTORICAL_EVENT for e in self.timeline):
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.MEDIUM,
                title="Current status of orders is unknown",
                statement=(
                    "These datasets record when an order was issued, served or rescinded, but "
                    "not whether it is enforceable today. DOB's disposition wording (for "
                    "example 'served' versus 'issued') can also differ from DOB's BIS pages. "
                    "Check DOB BIS / DOB NOW and HPD Online before relying on any order."
                ),
            )
        if self.b.mode == "demo" and not self.b.in_demo_dataset:
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNAVAILABLE,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.HIGH,
                title="No recorded records for this address",
                statement=(
                    "The address was resolved, but the offline demo only holds records for "
                    "five recorded buildings. No source was checked for this one, so nothing "
                    "here says whether it has violations or complaints. Run the app in live "
                    "mode to check it."
                ),
            )
        elif self.b.mode == "demo":
            self.add_finding(
                section=SectionId.DATA_GAPS,
                category=Category.UNCERTAIN,
                scope=Scope.NOT_APPLICABLE,
                importance=Importance.INFO,
                title="Demo data snapshot",
                statement=(
                    "This report uses sanitized NYC Open Data responses recorded between "
                    "2026-10-21 and 2026-10-23. Records added or changed since then are not "
                    "shown."
                ),
            )

    # ---- resolution / summary ---------------------------------------------------------

    def resolution_view(self) -> ResolutionView:
        res = self.res
        b = res.building
        if res.outcome is Outcome.VERIFIED and b is not None:
            if self.building_level:
                conf, label = "high", "High: tax lot and building (BIN) verified in two sources"
            else:
                conf, label = (
                    "medium",
                    "Medium: tax lot verified; building not individually identified",
                )
        elif res.outcome is Outcome.AMBIGUOUS:
            conf, label = "needs_choice", "Needs your choice: more than one building matches"
        else:
            conf, label = "none", "Not resolved: no building was selected"
        links: list[OfficialLink] = []
        if b is not None:
            boro, block, lot = b.bbl[0], int(b.bbl[1:6]), int(b.bbl[6:])
            links.append(
                OfficialLink(
                    label="ZoLa tax lot page (NYC Planning)",
                    url=f"https://zola.planning.nyc.gov/l/lot/{boro}/{block}/{lot}",
                )
            )
            if b.bin and self.building_level:
                links.append(
                    OfficialLink(
                        label="DOB BIS property profile",
                        url=f"https://a810-bisweb.nyc.gov/bisweb/PropertyProfileOverviewServlet?bin={b.bin}",
                        note="Check current DOB orders and complaints here.",
                    )
                )
            bld = self.b.hpd_buildings
            rb = bld.summary.requested_building if bld and bld.summary else None
            if rb and rb.building_id:
                links.append(
                    OfficialLink(
                        label="HPD Online building page",
                        url=f"https://hpdonline.nyc.gov/hpdonline/building/{rb.building_id}",
                        note="Check current HPD violations, complaints and registration here.",
                    )
                )
        return ResolutionView(
            outcome=res.outcome.value,
            explanation=res.explanation,
            level=res.resolution_level.value if res.resolution_level else None,
            confidence=conf,
            confidence_label=label,
            bbl=b.bbl if b else None,
            bin=b.bin if b else None,
            label=b.label if b else None,
            borough=b.borough if b else None,
            bin_evidence=b.bin_evidence.value if b else None,
            reasons=[r.value for r in res.reasons],
            permitted_join_keys=res.permitted_join_keys,
            footprint_bins_on_lot=(b.footprint_bins_on_lot or []) if b else [],
            options=[
                ResolutionOption(
                    bbl=o.bbl,
                    bin=o.bin,
                    label=o.label,
                    borough=o.borough,
                    bin_evidence=o.bin_evidence.value,
                )
                for o in res.options
            ],
            user_selected=bool(self.b.selected_option) and res.outcome is Outcome.VERIFIED,
            in_demo_dataset=self.b.in_demo_dataset,
            links=links,
        )

    def questions(self) -> list[str]:
        q: list[str] = []
        fs = {f.title: f for f in self.findings}
        if "Open immediately hazardous (Class C) violations" in fs:
            n = int(fs["Open immediately hazardous (Class C) violations"].fact.value)
            q.append(
                f"HPD's data shows {n} open Class C violations. Ask which have been fixed, "
                "whether correction was certified to HPD, and for a timeline for the rest."
            )
        elif "Open HPD violations" in fs:
            q.append("Ask how and when the open HPD violations will be corrected.")
        if "Heat and hot water complaints (last 3 years)" in fs:
            q.append(
                "Heat or hot water complaints appear in recent years. Ask how heat and hot "
                "water are supplied and what happened during the last outages."
            )
        if any(f.section is SectionId.ORDERS for f in self.findings):
            q.append(
                "The records include order events (for example vacate, stop-work or AEP). Ask "
                "whether each has been lifted or completed, and confirm on DOB BIS / HPD "
                "Online."
            )
        if any(f.section is SectionId.OTHER_BUILDINGS for f in self.findings):
            q.append(
                "Other buildings share this tax lot. Confirm which building and entrance your "
                "apartment is in, so you read the right records."
            )
        if (
            "Summonses DOB lists as not yet resolved" in fs
            and fs["Summonses DOB lists as not yet resolved"].fact.value
        ):
            q.append("Ask about the ECB summonses DOB still lists as active.")
        if not self.building_level and self.res.outcome is Outcome.VERIFIED:
            q.append(
                "Only the tax lot was verified. Ask for the building's BIN or certificate of "
                "occupancy so building-level records can be checked."
            )
        q.append("Ask who the managing agent is and how repair requests are handled.")
        q.append(
            "Ask whether the apartment is rent stabilized and request the lease and rent "
            "history (this report does not determine rent stabilization)."
        )
        return q

    def build(self) -> ResearchReport:
        self.stats: list[Stat] = []
        self.identity()
        overview = None
        if self.res.outcome is Outcome.VERIFIED and self.res.building is not None:
            overview = self.registration()
            self.hpd_violations()
            self.hpd_complaints()
            self.dob_complaints()
            self.ecb()
            self.sr311()
            self.data_gaps()
        # order sources consistently
        self.sources.sort(key=lambda s: SOURCE_ORDER.index(s.source))
        self.timeline.sort(key=lambda t: (t.date, t.evidence_id), reverse=True)
        sections = []
        key = [
            f.id
            for f in sorted(
                self.findings,
                key=lambda f: (
                    {"high": 0, "medium": 1, "info": 2}[f.importance.value],
                    list(SectionId).index(f.section),
                ),
            )
            if f.importance is not Importance.INFO and f.category is not Category.IDENTITY
        ][:6]
        for sid in SectionId:
            if sid is SectionId.SUMMARY:
                ids = key
            else:
                ids = [f.id for f in self.findings if f.section is sid]
            if not ids and sid not in (SectionId.SUMMARY, SectionId.IDENTITY):
                continue
            sections.append(
                Section(
                    id=sid, title=SECTION_TITLES[sid], intro=SECTION_INTROS[sid], finding_ids=ids
                )
            )
        rv = self.resolution_view()
        report = ResearchReport(
            report_id=uuid.uuid4().hex[:12],
            generated_at=self.now.isoformat(timespec="seconds"),
            input_address=self.b.address,
            demo_notice=(
                "Demo mode: sanitized NYC Open Data recorded 21 to 23 October 2026. No live "
                "API calls were made."
                if self.b.mode == "demo"
                else None
            ),
            resolution=rv,
            building=overview,
            headline=self.headline(rv),
            stats=self.stats,
            key_finding_ids=key,
            sections=sections,
            findings=self.findings,
            evidence=list(self.evidence.values()),
            timeline=self.timeline,
            sources=self.sources,
            questions_to_ask=self.questions() if self.res.outcome is Outcome.VERIFIED else [],
            limitations=LIMITATIONS,
            run=RunInfo(
                mode=self.b.mode,
                method="deterministic_baseline",
                elapsed_seconds=self.b.elapsed_seconds,
                external_requests=self.b.external_requests,
            ),
        )
        return report

    def headline(self, rv: ResolutionView) -> str:
        if rv.outcome == "verified":
            where = rv.label or self.b.address
            if not self.b.in_demo_dataset:
                return f"{where}: resolved, but not in the offline demo dataset."
            if rv.level == "building":
                return f"{where}: building verified (BIN {rv.bin})."
            return f"{where}: tax lot verified; building-level claims withheld."
        if rv.outcome == "ambiguous":
            return "More than one building matches this address. Choose one to continue."
        if not self.b.in_demo_dataset:
            return "This address is not in the offline demo dataset."
        if rv.outcome == "source_unavailable":
            return "The address could not be checked because an official source was unavailable."
        return "No building could be reliably matched to this address, so nothing was attached."


LIMITATIONS = [
    "Covers HPD violations, HPD complaints, HPD building and registration records, DOB "
    "complaints, DOB ECB summonses and 311 requests (other agencies, since 2020). It does not "
    "cover DOB NOW filings, OATH records beyond what DOB publishes, court cases, rent "
    "stabilization, ownership or lead-paint history.",
    "Open data can lag behind the agencies' own systems and can contain errors. 'Open' and "
    "'active' describe the record, not the physical condition today.",
    "Complaints and 311 requests are reports by members of the public, not findings.",
    "Unit numbers, complainant details and free-text descriptions that could identify a "
    "household are withheld or redacted.",
    "Address matching is conservative: when more than one building could match, or the "
    "building cannot be verified, the report asks or limits itself to the tax lot.",
    "Manual validation of the source mappings covered a risk-based sample, not every record.",
]


def build_baseline_report(bundle: EvidenceBundle, *, now: dt.datetime | None = None):
    return ReportBuilder(bundle, now=now).build()


def demo_examples() -> list[dict]:
    return [
        {
            "key": "west_farms_1134",
            "address": DEMO_ADDRESSES["west_farms_1134"],
            "why": "Bronx walk-up with a long HPD history and a second building on the lot",
        },
        {
            "key": "acp_1845",
            "address": DEMO_ADDRESSES["acp_1845"],
            "why": "Harlem condominium: records filed under the condo billing lot",
        },
        {
            "key": "west_farms_1217",
            "address": DEMO_ADDRESSES["west_farms_1217"],
            "why": "Only the tax lot can be verified, so building claims are withheld",
        },
        {
            "key": "west_17_220",
            "address": DEMO_ADDRESSES["west_17_220"],
            "why": "Chelsea lot with eight buildings; only one is the requested building",
        },
        {
            "key": "fifth_ave_350",
            "address": DEMO_ADDRESSES["fifth_ave_350"],
            "why": "Empire State Building: no HPD violations, many DOB/ECB records",
        },
    ]
