"""Evidence verification for findings (baseline) and for agent-written sentences.

Findings: every cited id must exist; the cited records' building attribution must match
the finding's scope; a FactCheck is re-computed from the evidence list; and statements are
scanned for wording the project never allows (safety verdicts, "currently in effect" orders).

Agent sentences: same citation and scope checks, plus every number in the sentence must be
supported by the cited findings or cited records. A sentence that fails is dropped, never
repaired.
"""

from __future__ import annotations

import re

from .schema import (
    AgentParagraph,
    EvidenceItem,
    Finding,
    RejectedClaim,
    ResearchReport,
    Scope,
    Verification,
    VerificationIssue,
)

# Which record attributions a finding of each scope may cite.
ALLOWED_ATTRIBUTIONS: dict[Scope, set[str]] = {
    Scope.THIS_BUILDING: {"this_building"},
    Scope.LOT_LEVEL: {"lot_level", "unattributed_on_lot"},
    Scope.OTHER_BUILDING_ON_LOT: {"other_building_on_lot"},
    Scope.UNATTRIBUTED_ON_LOT: {"unattributed_on_lot", "lot_level"},
    Scope.MIXED: {"mixed"},
    Scope.NOT_APPLICABLE: {
        "this_building",
        "lot_level",
        "unattributed_on_lot",
        "other_building_on_lot",
        "mixed",
    },
}

FORBIDDEN = [
    (
        re.compile(r"\b(safe|unsafe|dangerous|habitable|uninhabitable)\b(?!-)", re.I),
        "safety or habitability verdict",
    ),
    (
        re.compile(r"\b(should|shouldn't|should not|do not|don't)\s+(rent|sign|move)", re.I),
        "rent/no-rent recommendation",
    ),
    (
        re.compile(r"\b(slumlord|negligent|criminal|illegal(ly)?|fraud\w*)\b", re.I),
        "unsupported accusation",
    ),
    (
        re.compile(r"\brent[- ]stabili[sz]ed\b(?!.{0,40}\b(not|whether|ask)\b)", re.I),
        "rent-stabilization claim",
    ),
    (re.compile(r"\bguarantee[sd]?\b", re.I), "guarantee"),
]

# "in effect / currently active" about an order, without a negation or hedge nearby.
ORDER_WORDS = re.compile(r"\b(order|vacate|stop[- ]work|padlock|aep)\b", re.I)
CURRENT_WORDS = re.compile(
    r"\b(currently|still|remains?|now|today|presently)\b.{0,25}\b(in effect|active|enforced|"
    r"in force|open)\b|\b(in effect|in force)\b|\bactive (vacate|stop[- ]work|order)",
    re.I,
)
HEDGE_WORDS = re.compile(
    r"\b(not|no|whether|unknown|cannot|can't|doesn't|does not|do not|unclear|check|verify)\b",
    re.I,
)

NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")


def _source_terms(items: list[EvidenceItem]) -> set[str]:
    """Labels that come verbatim from the agencies (e.g. the 311 type 'Illegal Parking')."""
    terms: set[str] = set()
    for e in items:
        for k in (
            "Complaint type",
            "Descriptor",
            "Category",
            "Problem categories",
            "Violation type",
            "Description (unit detail redacted)",
            "Infraction codes",
            "Disposition (DOB's text)",
        ):
            v = e.details.get(k)
            if isinstance(v, str) and v:
                terms |= {t.strip() for t in v.split(",") if len(t.strip()) > 3}
    return terms


def wording_problems(text: str, source_terms: set[str] | frozenset = frozenset()) -> list[str]:
    scrubbed = text
    for t in sorted(source_terms, key=len, reverse=True):
        scrubbed = re.sub(re.escape(t), " ", scrubbed, flags=re.I)
    text = scrubbed
    problems = [why for pat, why in FORBIDDEN if pat.search(text)]
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if ORDER_WORDS.search(sentence) and CURRENT_WORDS.search(sentence):
            if not HEDGE_WORDS.search(sentence):
                problems.append("historical order presented as current")
    return problems


def _match(e: EvidenceItem, where: dict) -> bool:
    if e.source != where.get("source"):
        return False
    attrs = where.get("attribution")
    if attrs is not None and e.attribution not in attrs:
        return False
    tags = set(e.tags)
    if not set(where.get("tags_all", [])) <= tags:
        return False
    tags_any = set(where.get("tags_any", []))
    if tags_any and not (tags & tags_any):
        return False
    pos = where.get("amount_positive")
    if pos and not e.amounts.get(pos, 0) > 0:
        return False
    return True


def check_finding(f: Finding, index: dict[str, EvidenceItem]) -> list[str]:
    problems: list[str] = []
    missing = [i for i in f.evidence_ids if i not in index]
    if missing:
        problems.append(f"cites {len(missing)} unknown evidence id(s), e.g. {missing[0]}")
    allowed = ALLOWED_ATTRIBUTIONS[f.scope]
    wrong = [
        i
        for i in f.evidence_ids
        if i in index
        and index[i].attribution not in allowed
        and index[i].source not in ("hpd_buildings", "hpd_registrations")
    ]
    if wrong:
        problems.append(
            f"scope {f.scope.value} cites {len(wrong)} record(s) attributed elsewhere, e.g. "
            f"{wrong[0]} ({index[wrong[0]].attribution})"
        )
    if f.fact is not None:
        matched = [e for e in index.values() if _match(e, f.fact.where)]
        if f.fact.metric == "count":
            if len(matched) != f.fact.value:
                problems.append(f"fact says {f.fact.value:g}, evidence gives {len(matched)}")
        elif f.fact.metric == "sum":
            total = sum(e.amounts.get(f.fact.field or "", 0) for e in matched)
            if abs(total - f.fact.value) > 0.005:
                problems.append(f"fact sum {f.fact.value:g} != evidence sum {total:g}")
        if {e.id for e in matched} != set(f.evidence_ids):
            problems.append("cited evidence differs from the records the fact selects")
        # Every number in the statement must equal the fact or be supportable text.
    problems += wording_problems(
        f.statement, _source_terms([index[i] for i in f.evidence_ids if i in index])
    )
    return problems


def verify_report(report: ResearchReport) -> Verification:
    index = report.evidence_index()
    v = Verification()
    for f in report.findings:
        v.findings_checked += 1
        v.citations_checked += len(f.evidence_ids)
        if f.fact is not None:
            v.facts_checked += 1
        for p in check_finding(f, index):
            v.issues.append(VerificationIssue(finding_id=f.id, problem=p))
    # Every timeline event must point at real evidence.
    for t in report.timeline:
        if t.evidence_id not in index:
            v.issues.append(
                VerificationIssue(
                    finding_id=None, problem=f"timeline cites unknown {t.evidence_id}"
                )
            )
    for p in check_attention(report, index):
        v.issues.append(VerificationIssue(finding_id=None, problem=p))
    v.passed = not v.issues
    return v


def check_attention(report: ResearchReport, index: dict[str, EvidenceItem]) -> list[str]:
    """Recompute every number in the Building Attention Summary from the evidence.

    Each point's facts are recounted; the evidence it cites must exist and be attributed
    to the requested building (or lot); every number in its text must equal one of its
    facts; and the wording is scanned like any finding.
    """
    a = report.attention
    if a is None:
        return []
    problems: list[str] = []
    allowed = (
        {"this_building"}
        if report.resolution.level == "building"
        else {
            "lot_level",
            "unattributed_on_lot",
        }
    )
    texts = [a.headline, a.label_meaning, *a.reasons]
    for p in a.points:
        values: set[str] = set()
        for af in p.facts:
            matched = [e for e in index.values() if _match(e, af.fact.where)]
            if len(matched) != af.fact.value:
                problems.append(
                    f"attention {p.id}: {af.name} says {af.fact.value:g}, "
                    f"evidence gives {len(matched)}"
                )
            if {e.id for e in matched} != set(af.evidence_ids):
                problems.append(f"attention {p.id}: {af.name} cites different records")
            if set(af.fact.where.get("attribution", [])) - allowed:
                problems.append(f"attention {p.id}: {af.name} counts records of other buildings")
            values.add(f"{af.fact.value:g}")
        unknown = [i for i in p.evidence_ids if i not in index]
        if unknown:
            problems.append(f"attention {p.id}: cites unknown evidence {unknown[0]}")
        if p.kind != "other_buildings":
            wrong = [
                i for i in p.evidence_ids if i in index and index[i].attribution not in allowed
            ]
            if wrong:
                problems.append(f"attention {p.id}: cites records of another building")
        scrubbed = re.sub(r"last (12 months|3 years)|Class 1", " ", p.text)
        stray = {n for n in _numbers(scrubbed) if n not in values}
        if stray:
            problems.append(f"attention {p.id}: unsupported number(s) {sorted(stray)}")
        texts += [p.text, p.ask or ""]
    for t in texts:
        for w in wording_problems(t):
            problems.append(f"attention wording: {w}: {t[:80]}")
    return problems


# ---- agent sentences ------------------------------------------------------------------


def _numbers(text: str) -> set[str]:
    out = set()
    for m in NUMBER.findall(text):
        n = m.replace("$", "").replace(",", "")
        if n.endswith(".00"):
            n = n[:-3]
        out.add(n)
    return out


def _support_numbers(
    paragraph: AgentParagraph, report: ResearchReport, index: dict[str, EvidenceItem]
) -> set[str]:
    allowed: set[str] = set()
    for fid in paragraph.finding_ids:
        f = report.finding(fid)
        if f is None:
            continue
        allowed |= _numbers(f.statement)
        if f.fact is not None:
            allowed.add(f"{f.fact.value:g}")
    for eid in paragraph.evidence_ids:
        e = index.get(eid)
        if e is None:
            continue
        blob = " ".join(
            [e.title, e.date or "", e.status or "", e.record_id]
            + [str(v) for v in e.details.values() if v is not None]
            + [f"{v:g}" for v in e.amounts.values()]
        )
        allowed |= _numbers(blob)
        if e.date:
            y, m, d = e.date.split("-")
            allowed |= {y, str(int(m)), str(int(d))}
    # Dates written as 2025-02-27 tokenize into parts; allow their parts too.
    for n in list(allowed):
        if "-" in n:
            allowed |= set(n.split("-"))
    allowed.add("311")  # the service's name, not a count
    return allowed


OTHER_BUILDING_WORDS = re.compile(
    r"\b(another|other|different|second|neighbou?ring|adjacent|separate)\b.{0,20}\b(building|bin|"
    r"structure)\b|\bBIN \d{7}\b",
    re.I,
)


def check_paragraph(
    p: AgentParagraph, report: ResearchReport, index: dict[str, EvidenceItem]
) -> list[str]:
    problems: list[str] = []
    if not p.text.strip():
        return ["empty"]
    if not p.evidence_ids and not p.finding_ids:
        problems.append("no citation")
    unknown = [i for i in p.evidence_ids if i not in index]
    unknown += [i for i in p.finding_ids if report.finding(i) is None]
    if unknown:
        problems.append(f"cites unknown id(s): {', '.join(unknown[:3])}")
    cited = [index[i] for i in p.evidence_ids if i in index]
    cited_findings = [report.finding(i) for i in p.finding_ids if report.finding(i)]
    touches_other = any(e.attribution == "other_building_on_lot" for e in cited) or any(
        f.scope is Scope.OTHER_BUILDING_ON_LOT for f in cited_findings
    )
    if touches_other and not OTHER_BUILDING_WORDS.search(p.text):
        problems.append("uses another building's records without saying so")
    if report.resolution.level == "tax_lot" and re.search(r"\bthis building\b", p.text, re.I):
        problems.append("building-specific claim on a tax-lot-only resolution")
    allowed = _support_numbers(p, report, index)
    unsupported = sorted(n for n in _numbers(p.text) if n not in allowed)
    if unsupported:
        problems.append(f"number(s) not supported by cited evidence: {', '.join(unsupported[:4])}")
    problems += wording_problems(p.text, _source_terms(cited))
    return problems


def verify_paragraphs(
    paragraphs: list[AgentParagraph], report: ResearchReport
) -> tuple[list[AgentParagraph], list[RejectedClaim]]:
    index = report.evidence_index()
    kept, rejected = [], []
    for p in paragraphs:
        problems = check_paragraph(p, report, index)
        if problems:
            rejected.append(RejectedClaim(text=p.text, reasons=problems))
        else:
            kept.append(p)
    return kept, rejected
