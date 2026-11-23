"""Building Attention Summary: a plain-English, rule-based reading of the cited records.

The summary answers "what deserves follow-up?" for a renter. It is deliberately NOT a
safety grade, a probability or a recommendation to rent or not rent.

How it works (also returned to the user as `rules`):

- Only records attributed to the requested building count (or, for a tax-lot-only match,
  the lot). Records on other buildings on the same lot are listed separately and never
  counted.
- Every number in the summary comes from a FactCheck over the report's evidence, and the
  verification layer recomputes it (verify.check_attention).
- Labels, in the order they are decided:
  1. Insufficient information: only the tax lot (not the building) was verified.
  2. Priority follow-up: at least one priority trigger in checked data:
       - an open HPD Class C ("immediately hazardous") violation, or
       - a DOB/ECB summons DOB lists as not yet resolved that DOB marks hazardous / Class 1.
  3. Insufficient information: a key source (HPD violations, HPD complaints, DOB
     complaints, ECB summonses) could not be checked.
  4. Some items to check: open Class A/B HPD violations, HPD complaints in the last 12
     months, DOB complaints in the last 3 years, unresolved summonses, or recorded order
     events (historical).
  5. Insufficient information: a key source was only partly retrieved, so "nothing found"
     would overstate what was checked.
  6. No priority items found in checked data.
- Complaints and 311 requests are always described as reports, not findings. Order events
  are always described as historical. Record counts alone never raise the label: only the
  agencies' own hazard classes do. No neighbourhood, demographic or personal data is used.
"""

from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime

from .schema import (
    AttentionFact,
    AttentionPoint,
    AttentionSummary,
    CoverageSource,
    DataCoverage,
    EvidenceItem,
    FactCheck,
    SectionId,
    SourceStatus,
)

KEY_SOURCES = ("hpd_violations", "hpd_complaints", "dob_complaints", "ecb_violations")

PLAIN_SOURCE = {
    "hpd_violations": "HPD housing-code violations",
    "hpd_complaints": "HPD complaints",
    "hpd_buildings": "HPD building records",
    "hpd_registrations": "HPD registration",
    "dob_complaints": "DOB complaints",
    "ecb_violations": "DOB/ECB summonses",
    "sr311": "311 requests",
}

LABELS = {
    "priority_follow_up": "Priority follow-up",
    "some_items_to_check": "Some items to check",
    "no_priority_items": "No priority items found in checked data",
    "insufficient_information": "Insufficient information",
}

LABEL_MEANING = {
    "priority_follow_up": (
        "Significant records from the agencies' own most serious categories warrant prompt "
        "verification."
    ),
    "some_items_to_check": "There are records worth discussing with the landlord or checking.",
    "no_priority_items": (
        "None of the defined priority triggers appeared in sources that were checked "
        "completely. This is not a safety rating, and older or unlisted issues may exist."
    ),
    "insufficient_information": (
        "Missing, incomplete or not-building-specific data prevents a meaningful summary. "
        "This says nothing good or bad about the building."
    ),
}


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _iso_date(raw: str | None) -> str | None:
    """ISO text or an HTTP date ("Sun, 22 Nov 2026 ...") as YYYY-MM-DD."""
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return None


def _coverage(sources: list[SourceStatus]) -> DataCoverage:
    by = {s.source: s for s in sources}
    rows: list[CoverageSource] = []
    unavailable, partial = [], []
    for name in KEY_SOURCES:
        s = by.get(name)
        label = PLAIN_SOURCE[name]
        if s is None:
            rows.append(CoverageSource(source=name, name=label, state="not_checked", note=""))
            unavailable.append(label)
            continue
        if s.status in ("source_unavailable", "error", "not_in_demo"):
            state, note = "unavailable", "Could not be checked. This is not evidence of no records."
            unavailable.append(label)
        elif s.status == "unsupported_join":
            state, note = "not_applicable", "Not searchable without a verified building number."
        elif s.complete:
            state, note = "complete", "Checked completely."
        else:
            state = "partial"
            note = "Only partly retrieved, so counts are lower bounds."
            if "upstream_recent_gap" in (s.status_detail or ""):
                note = (
                    "The city's published dataset is currently missing recent complaints, so "
                    "counts are lower bounds."
                )
            partial.append(label)
        rows.append(CoverageSource(source=name, name=label, state=state, note=note))
    dates = sorted(d for d in (_iso_date(s.data_as_of) for s in sources) if d)
    if unavailable:
        level, label = "limited", "Limited: some sources could not be checked"
        detail = f"Not checked: {', '.join(unavailable)}."
    elif partial:
        level, label = "partial", "Partial: some sources only partly retrieved"
        detail = f"Partly retrieved: {', '.join(partial)}. Counts from them are lower bounds."
    else:
        level, label = "complete", "Complete for the key sources checked"
        detail = (
            "HPD violations, HPD complaints, DOB complaints and DOB/ECB summonses were checked."
        )
    return DataCoverage(
        level=level,
        label=label,
        detail=detail,
        sources=rows,
        oldest_data_as_of=dates[0] if dates else None,
        newest_data_as_of=dates[-1] if dates else None,
    )


def compute_attention(report) -> AttentionSummary | None:
    """Build the summary from a verified report. None when no building was verified."""
    rv = report.resolution
    if rv.outcome != "verified":
        return None
    building = rv.level == "building"
    attrs = ["this_building"] if building else ["lot_level", "unattributed_on_lot"]
    ev: list[EvidenceItem] = report.evidence
    where_word = "this building" if building else "this tax lot"

    def fact(name: str, source: str, tags_all=(), tags_any=()) -> AttentionFact:
        where = {"source": source, "attribution": attrs, "tags_all": list(tags_all)}
        if tags_any:
            where["tags_any"] = list(tags_any)
        ids = [
            e.id
            for e in ev
            if e.source == source
            and e.attribution in attrs
            and set(tags_all) <= set(e.tags)
            and (not tags_any or set(tags_any) & set(e.tags))
        ]
        return AttentionFact(
            name=name,
            fact=FactCheck(metric="count", value=len(ids), where=where),
            evidence_ids=ids,
        )

    def val(f: AttentionFact) -> int:
        return int(f.fact.value)

    def findings_in(section: SectionId) -> list[str]:
        return [f.id for f in report.findings if f.section is section]

    points: list[AttentionPoint] = []
    priority_reasons: list[str] = []
    check_reasons: list[str] = []

    # 1. HPD open violations
    v_open = fact("open", "hpd_violations", ["open"], ["class:A", "class:B", "class:C"])
    v_c = fact("open_class_c", "hpd_violations", ["open", "class:C"])
    v_b = fact("open_class_b", "hpd_violations", ["open", "class:B"])
    if val(v_open):
        parts = []
        if val(v_c):
            parts.append(
                f"{val(v_c)} in Class C, HPD's most serious (immediately hazardous) category"
            )
        if val(v_b):
            parts.append(f"{val(v_b)} in Class B (hazardous)")
        incl = f", including {' and '.join(parts)}" if parts else ""
        level = "priority" if val(v_c) else "check"
        (priority_reasons if val(v_c) else check_reasons).append(
            f"{_n(val(v_c), 'open Class C violation')} in HPD data"
            if val(v_c)
            else f"{_n(val(v_open), 'open HPD violation')}"
        )
        points.append(
            AttentionPoint(
                id="A-hpd-violations",
                kind="housing_violations",
                level=level,
                heading="Housing violations",
                text=(
                    f"HPD, NYC's housing maintenance agency, lists "
                    f"{_n(val(v_open), 'open violation')} for {where_word}{incl}. Open means HPD "
                    "has not marked them corrected; it does not prove each condition exists "
                    "today."
                ),
                ask="Ask which open violations have been fixed and whether the repairs were "
                "certified to HPD.",
                section="hpd_violations",
                facts=[v_open, v_c, v_b],
                evidence_ids=v_open.evidence_ids,
                finding_ids=findings_in(SectionId.HPD_VIOLATIONS),
            )
        )

    # 2. DOB/ECB summonses DOB lists as unresolved
    e_open = fact("unresolved", "ecb_violations", ["correction:open_with_dob"])
    e_haz = fact(
        "unresolved_hazardous",
        "ecb_violations",
        ["correction:open_with_dob", "hazardous_or_class1"],
    )
    if val(e_open):
        haz = ""
        if val(e_haz) and val(e_haz) == val(e_open):
            haz = ", marked hazardous or Class 1 by DOB"
        elif val(e_haz):
            haz = f", {val(e_haz)} of them marked hazardous or Class 1 by DOB"
        if val(e_haz):
            priority_reasons.append(
                _n(val(e_haz), "unresolved hazardous summons", "unresolved hazardous summonses")
            )
        else:
            check_reasons.append(f"{_n(val(e_open), 'unresolved summons', 'unresolved summonses')}")
        points.append(
            AttentionPoint(
                id="A-ecb",
                kind="dob_enforcement",
                level="priority" if val(e_haz) else "check",
                heading="Building-code summonses",
                text=(
                    f"DOB, NYC's building safety and construction department, lists "
                    f"{_n(val(e_open), 'summons', 'summonses')} (ECB) for {where_word} as not "
                    f"yet resolved{haz}. A summons is a charge issued by an agency, not "
                    "necessarily a final finding."
                ),
                ask="Ask what each open summons was for and whether the condition has been "
                "corrected with DOB.",
                section="ecb_violations",
                facts=[e_open, e_haz],
                evidence_ids=e_open.evidence_ids,
                finding_ids=findings_in(SectionId.ECB_VIOLATIONS),
            )
        )

    # 3. HPD complaints, last 12 months
    c12 = fact("recent_12m", "hpd_complaints", ["recent_12m"])
    if val(c12):
        check_reasons.append(f"{_n(val(c12), 'HPD complaint')} in the last 12 months")
        cats: dict[str, int] = {}
        for e in ev:
            if e.id in set(c12.evidence_ids):
                for t in e.tags:
                    if t.startswith("category:"):
                        cats[t[9:]] = cats.get(t[9:], 0) + 1
        top = max(cats, key=lambda k: cats[k]).lower() if cats else None
        about = ""
        if top:
            about = (
                f" It was about {top}." if val(c12) == 1 else f" The most common topic was {top}."
            )
        points.append(
            AttentionPoint(
                id="A-hpd-complaints",
                kind="resident_reports",
                level="check",
                heading="Resident-reported issues",
                text=(
                    f"{_n(val(c12), 'complaint')} to HPD about {where_word} "
                    f"{'was' if val(c12) == 1 else 'were'} recorded in the last 12 months."
                    f"{about} Complaints are reports from residents or others, not proof of a "
                    "problem."
                ),
                ask="Ask what the recent complaints were about and how they were handled.",
                section="hpd_complaints",
                facts=[c12],
                evidence_ids=c12.evidence_ids,
                finding_ids=findings_in(SectionId.HPD_COMPLAINTS),
            )
        )

    # 4. DOB complaints, last 3 years
    d36 = fact("recent_36m", "dob_complaints", ["recent_36m"])
    if val(d36):
        check_reasons.append(f"{_n(val(d36), 'DOB complaint')} in the last 3 years")
        points.append(
            AttentionPoint(
                id="A-dob-complaints",
                kind="dob_complaints",
                level="check",
                heading="Building complaints to DOB",
                text=(
                    f"{_n(val(d36), 'complaint')} about {where_word} "
                    f"{'was' if val(d36) == 1 else 'were'} made to DOB in the last 3 years. "
                    "These are reports, not findings; the outcome of each is shown in the "
                    "details."
                ),
                ask="Ask about any recent construction, structural or safety complaints.",
                section="dob_complaints",
                facts=[d36],
                evidence_ids=d36.evidence_ids,
                finding_ids=findings_in(SectionId.DOB_COMPLAINTS),
            )
        )

    # 5. Recorded order events: always historical
    orders = fact("order_events", "dob_complaints", ["order_event"])
    if val(orders):
        check_reasons.append(f"{_n(val(orders), 'recorded order event')} (historical)")
        points.append(
            AttentionPoint(
                id="A-orders",
                kind="historical_orders",
                level="check",
                heading="Recorded orders (historical)",
                text=(
                    f"DOB's complaint data records {_n(val(orders), 'order event')} for "
                    f"{where_word}, such as a vacate or stop-work order. These are historical "
                    "records; this data does not show whether any order applies today."
                ),
                ask="Ask whether each order was lifted, and confirm on DOB's Building "
                "Information System.",
                section="orders",
                facts=[orders],
                evidence_ids=orders.evidence_ids,
                finding_ids=findings_in(SectionId.ORDERS),
            )
        )

    # 6. Other buildings on the lot (never counted above)
    other = [e.id for e in ev if e.attribution == "other_building_on_lot"]
    if building and other:
        points.append(
            AttentionPoint(
                id="A-other-buildings",
                kind="other_buildings",
                level="info",
                heading="Other buildings on this lot",
                text=(
                    "This tax lot holds more than one building. Records filed for the other "
                    "buildings are listed separately and are not counted in this summary."
                ),
                ask="Confirm which building and entrance the apartment is in.",
                section="other_buildings",
                evidence_ids=other,
                finding_ids=findings_in(SectionId.OTHER_BUILDINGS),
            )
        )

    # 7. HPD has no residential record (commercial, small or unregistered buildings)
    srcs = {s.source: s for s in report.sources}
    hb = srcs.get("hpd_buildings")
    if hb is not None and hb.status == "no_records_found":
        points.append(
            AttentionPoint(
                id="A-no-hpd-record",
                kind="hpd_not_applicable",
                level="info",
                heading="No HPD residential record",
                text=(
                    "HPD has no residential building record for this address. HPD datasets "
                    "cover residential buildings, so they may simply not apply here (for "
                    "example a commercial building). Empty HPD results are not evidence "
                    "about conditions."
                ),
                ask="Ask whether the building is registered with HPD as a multiple dwelling.",
                section="sources",
            )
        )

    coverage = _coverage(report.sources)
    reasons: list[str]
    if not building:
        label = "insufficient_information"
        reasons = [
            "Only the tax lot was verified, so records cannot be tied to this specific building."
        ]
    elif priority_reasons:
        label, reasons = "priority_follow_up", priority_reasons + check_reasons
    elif coverage.level == "limited":
        label, reasons = "insufficient_information", [coverage.detail]
    elif check_reasons:
        label, reasons = "some_items_to_check", check_reasons
    elif coverage.level == "partial":
        label, reasons = "insufficient_information", [coverage.detail]
    else:
        label = "no_priority_items"
        reasons = ["No open violations, recent complaints, unresolved summonses or order events."]

    headline = {
        "priority_follow_up": "Several records deserve follow-up before signing a lease."
        if len(priority_reasons) + len(check_reasons) > 1
        else "One record deserves follow-up before signing a lease.",
        "some_items_to_check": "Some records are worth asking the landlord about.",
        "no_priority_items": (
            "No priority records were found in the checked sources. Older, closed records may "
            "still be listed below."
        ),
        "insufficient_information": (
            "Stoop could not check enough data to summarize this building with confidence."
        ),
    }[label]

    if not [p for p in points if p.level != "info"]:
        points.insert(
            0,
            AttentionPoint(
                id="A-none",
                kind="no_priority",
                level="info",
                heading="Nothing flagged in checked data",
                text=(
                    "No open HPD violations, HPD complaints in the last 12 months, DOB "
                    "complaints in the last 3 years, unresolved summonses or order events "
                    f"were found for {where_word} in the sources that could be checked. Older, "
                    "closed records may still appear in the full report."
                ),
                section="sources",
            ),
        )

    order = {"priority": 0, "check": 1, "info": 2}
    points.sort(key=lambda p: order[p.level])
    next_steps = list(report.questions_to_ask[:4])
    return AttentionSummary(
        label=label,
        label_text=LABELS[label],
        label_meaning=LABEL_MEANING[label],
        headline=headline,
        reasons=reasons,
        points=points,
        coverage=coverage,
        next_steps=next_steps,
        verify_links=list(rv.links),
    )
