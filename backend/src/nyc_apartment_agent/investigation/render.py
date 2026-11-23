"""Markdown export of a ResearchReport (same content and order as the web report)."""

from __future__ import annotations

from .schema import ResearchReport

CATEGORY_LABEL = {
    "identity": "Identity",
    "verified_record": "Verified record",
    "complaint_allegation": "Complaint (allegation)",
    "enforcement_action": "Enforcement action",
    "historical_event": "Historical event",
    "administrative_record": "Administrative record",
    "uncertain": "Uncertain",
    "unavailable": "Unavailable",
}

MAX_CITATIONS = 8


def render_markdown(report: ResearchReport) -> str:
    ev = report.evidence_index()
    out: list[str] = []
    r = report.resolution
    out.append("# NYC Apartment Research Report")
    out.append("")
    out.append(f"**Address searched:** {report.input_address}  ")
    out.append(
        f"**Generated:** {report.generated_at} · report `{report.report_id}` · "
        f"method: {report.run.method.replace('_', ' ')} · mode: {report.run.mode}"
    )
    out.append("")
    if report.demo_notice:
        out.append(f"> **Demo data.** {report.demo_notice}")
        out.append("")
    out.append(f"> {report.disclaimer}")
    out.append("")
    out.append(f"## {report.headline}")
    out.append("")
    out.append(f"- **Match:** {r.outcome} ({r.confidence_label})")
    if r.bbl:
        out.append(f"- **Tax lot (BBL):** {r.bbl}" + (f" · **BIN:** {r.bin}" if r.bin else ""))
    for link in r.links:
        out.append(f"- [{link.label}]({link.url})")
    if r.options:
        out.append(
            "- **Possible matches:** "
            + "; ".join(f"{o.label} (BBL {o.bbl}, BIN {o.bin or 'unverified'})" for o in r.options)
        )
    out.append("")
    if report.stats:
        out.append("| Measure | Value | Note |")
        out.append("|---|---|---|")
        for s in report.stats:
            out.append(f"| {s.label} | {s.value} | {s.hint or ''} |")
        out.append("")
    if report.agent and report.agent.paragraphs:
        out.append("## AI-assisted analysis (verified against cited records)")
        out.append("")
        for p in report.agent.paragraphs:
            cites = ", ".join(p.finding_ids + p.evidence_ids[:MAX_CITATIONS])
            out.append(f"{p.text} _[{cites}]_")
            out.append("")
    for sec in report.sections:
        out.append(f"## {sec.title}")
        out.append("")
        out.append(f"_{sec.intro}_")
        out.append("")
        for fid in sec.finding_ids:
            f = report.finding(fid)
            if f is None:
                continue
            out.append(
                f"- **{f.title}** ({CATEGORY_LABEL[f.category.value]}, "
                f"{f.scope.value.replace('_', ' ')}) {f.statement}"
            )
            if f.caveat:
                out.append(f"  - _Caveat:_ {f.caveat}")
            if f.evidence_ids:
                shown = []
                for eid in f.evidence_ids[:MAX_CITATIONS]:
                    e = ev.get(eid)
                    if e and e.source_url:
                        shown.append(f"[{eid}]({e.source_url})")
                    else:
                        shown.append(eid)
                more = len(f.evidence_ids) - MAX_CITATIONS
                out.append(
                    "  - Evidence: " + ", ".join(shown) + (f" and {more} more" if more > 0 else "")
                )
        out.append("")
    if report.questions_to_ask:
        out.append("## Questions to ask before renting")
        out.append("")
        out.extend(f"1. {q}" for q in report.questions_to_ask)
        out.append("")
    if report.sources:
        out.append("## Sources checked")
        out.append("")
        out.append("| Agency | Dataset | Status | Records | Data as of |")
        out.append("|---|---|---|---|---|")
        for s in report.sources:
            out.append(
                f"| {s.agency} | [{s.dataset_name}]({s.dataset_url}) | {s.status_label} "
                f"| {s.records} | {s.data_as_of or 'unknown'} |"
            )
        out.append("")
    notable = [t for t in report.timeline if t.notable][:25]
    if notable:
        out.append("## Notable events (most recent first)")
        out.append("")
        for t in notable:
            other = " (other building on lot)" if t.attribution == "other_building_on_lot" else ""
            out.append(f"- {t.date} · {t.agency} · {t.label}{other} [{t.evidence_id}]")
        out.append("")
    out.append("## Limitations")
    out.append("")
    out.extend(f"- {x}" for x in report.limitations)
    out.append("")
    v = report.verification
    out.append(
        f"_Verification: {v.findings_checked} findings, {v.facts_checked} counts "
        f"re-computed, {v.citations_checked} citations checked; "
        f"{'all passed' if v.passed else str(len(v.issues)) + ' issue(s)'}._"
    )
    return "\n".join(out) + "\n"
