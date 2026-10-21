"""Resolve typed text to one building, a set of options, or an explicit refusal.

Pipeline (see docs/resolver_design.md):
  parse -> GeoSearch -> (per-borough checks if no borough stated) -> group candidates
  -> independent checks -> decide -> Footprints cross-check -> explain.
"""

from __future__ import annotations

from ..errors import InvalidIdentifierError, SourceError
from ..http_client import SourceHttpClient
from ..identifiers import BinStatus
from ..sources import footprints, geosearch
from .checks import Group, borough_match, build_groups
from .models import (
    BinEvidence,
    BoroughMatch,
    CandidateCheck,
    NumberMatch,
    Outcome,
    ParsedInput,
    Reason,
    ResolutionLevel,
    ResolutionResult,
    ResolvedIdentity,
    StreetMatch,
)
from .normalize import BOROUGH_CODES, MAX_INPUT_CHARS, ParsedAddress, parse_address

MAIN_QUERY_SIZE = 10
BOROUGH_QUERY_SIZE = 10


def _parsed_model(p: ParsedAddress) -> ParsedInput:
    return ParsedInput(
        raw=p.raw,
        house_number=p.house_number,
        street=p.street,
        street_normalized=p.street_normalized,
        borough=p.borough,
        zip_code=p.zip_code,
        state=p.state,
        notes=p.notes,
    )


def _result(
    outcome: Outcome, explanation: str, reasons: list[Reason], parsed: ParsedAddress, **kw
) -> ResolutionResult:
    return ResolutionResult(
        outcome=outcome, explanation=explanation, reasons=reasons, input=_parsed_model(parsed), **kw
    )


def _street_text(parsed: ParsedAddress) -> str:
    return f"{parsed.house_number} {parsed.street}"


def resolve_address(
    text: str,
    geosearch_client: SourceHttpClient,
    socrata_client: SourceHttpClient,
) -> ResolutionResult:
    text = (text or "").strip()
    parsed = parse_address(text)
    provenance: dict = {"requests": []}

    # ---- 1. Input checks (no network) -------------------------------------------
    if not text:
        return _result(
            Outcome.NO_RELIABLE_MATCH, "No address was entered.", [Reason.INPUT_EMPTY], parsed
        )
    if len(text) > MAX_INPUT_CHARS:
        return _result(
            Outcome.NO_RELIABLE_MATCH,
            f"The input is longer than {MAX_INPUT_CHARS} characters.",
            [Reason.INPUT_TOO_LONG],
            parsed,
        )
    if parsed.state and parsed.state != "NY":
        return _result(
            Outcome.NO_RELIABLE_MATCH,
            f"The address is in {parsed.state}, not New York City.",
            [Reason.NOT_NYC],
            parsed,
        )
    if not parsed.house_number:
        return _result(
            Outcome.NO_RELIABLE_MATCH,
            "No house number was found. Add the building number, "
            "for example '350 5th Avenue, Manhattan'.",
            [Reason.MISSING_HOUSE_NUMBER],
            parsed,
        )

    # ---- 2. Main GeoSearch query --------------------------------------------------
    tagged: list[tuple[str, geosearch.GeoSearchCandidate]] = []
    try:
        main = geosearch.search(geosearch_client, text, size=MAIN_QUERY_SIZE)
    except SourceError as exc:
        return _result(
            Outcome.SOURCE_UNAVAILABLE,
            "The City's address service (GeoSearch) could not be reached, so "
            "no match can be confirmed or ruled out. Please try again.",
            [Reason.GEOSEARCH_UNAVAILABLE],
            parsed,
            provenance={"error": str(exc)},
        )
    provenance["requests"].append(main.response)
    tagged += [("main", c) for c in main.candidates]

    # ---- 3. Borough: stated, or inferred from per-borough checks -------------------
    inferred: str | None = None
    boroughs_with_hit: set[str] = set()
    failed_boroughs: list[str] = []
    if parsed.borough is None:
        for borough in BOROUGH_CODES:
            try:
                res = geosearch.search(
                    geosearch_client, f"{_street_text(parsed)}, {borough}", size=BOROUGH_QUERY_SIZE
                )
            except SourceError:
                failed_boroughs.append(borough)
                continue
            provenance["requests"].append(res.response)
            tagged += [(f"borough:{borough}", c) for c in res.candidates]
        for g in build_groups(tagged):
            if (
                g.best_number(parsed) is NumberMatch.EXACT
                and g.best_street(parsed) is StreetMatch.EXACT
            ):
                boroughs_with_hit.add(g.first.borough)  # the source's own borough field
        if len(boroughs_with_hit) == 1 and not failed_boroughs:
            inferred = next(iter(boroughs_with_hit))
        if failed_boroughs and not boroughs_with_hit:
            return _result(
                Outcome.SOURCE_UNAVAILABLE,
                "No borough was given and the borough checks for "
                f"{', '.join(failed_boroughs)} failed, so absence of a match is not proven.",
                [Reason.BOROUGH_CHECK_INCOMPLETE, Reason.GEOSEARCH_UNAVAILABLE],
                parsed,
                provenance=provenance,
            )

    # ---- 3b. Stated borough: if the main query found no exact address there, ask
    # GeoSearch again with only number + street + borough. The main query can be pulled
    # off course by other words (a wrong ZIP made it return only Manhattan results).
    if parsed.borough is not None and not _has_exact_in(tagged, parsed, parsed.borough):
        try:
            res = geosearch.search(
                geosearch_client,
                f"{_street_text(parsed)}, {parsed.borough}",
                size=BOROUGH_QUERY_SIZE,
            )
            provenance["requests"].append(res.response)
            tagged += [(f"borough:{parsed.borough}", c) for c in res.candidates]
        except SourceError as exc:
            return _result(
                Outcome.SOURCE_UNAVAILABLE,
                f"The follow-up address check for {parsed.borough} failed, so a match can be "
                "neither confirmed nor ruled out. Please try again.",
                [Reason.GEOSEARCH_UNAVAILABLE],
                parsed,
                provenance={**provenance, "error": str(exc)},
            )

    # ---- 4. Group and check every candidate ---------------------------------------
    groups = build_groups(tagged)
    checks: list[tuple[Group, CandidateCheck]] = []
    for g in groups:
        m = g.best_member(parsed)
        number, street = g.best_number(parsed), g.best_street(parsed)
        bmatch = borough_match(m.borough, parsed.borough, inferred)
        zip_conflict = bool(parsed.zip_code and m.postalcode and parsed.zip_code != m.postalcode)
        strict = (
            number is NumberMatch.EXACT
            and street is StreetMatch.EXACT
            and bmatch in (BoroughMatch.STATED_MATCH, BoroughMatch.INFERRED)
            and not zip_conflict
        )
        weak = (
            not strict
            and number in (NumberMatch.EXACT, NumberMatch.HYPHEN_ONLY)
            and street in (StreetMatch.EXACT, StreetMatch.NEAR)
        )
        checks.append(
            (
                g,
                CandidateCheck(
                    label=m.label,
                    aliases=g.labels,
                    house_number=m.housenumber,
                    street=m.street,
                    borough=m.borough,
                    postalcode=m.postalcode,
                    bbl=m.bbl,
                    pad_bin=m.bin,
                    pad_bin_status=m.bin_status.value,
                    number=number,
                    street_match=street,
                    borough_match=bmatch,
                    zip_conflict=zip_conflict,
                    strict=strict,
                    weak=weak,
                    found_by=sorted(g.found_by),
                ),
            )
        )
    candidate_models = [c for _, c in checks]
    strict = [(g, c) for g, c in checks if c.strict]
    weak = [(g, c) for g, c in checks if c.weak]
    common = dict(candidates=candidate_models, provenance=provenance)

    # ---- 5. Decide ----------------------------------------------------------------
    if len(strict) > 1:
        return _result(
            Outcome.AMBIGUOUS,
            f"{len(strict)} different PAD records match this address exactly; "
            "please choose the right one.",
            [Reason.MULTIPLE_BUILDINGS],
            parsed,
            options=[_option(g, c, parsed, inferred) for g, c in strict],
            **common,
        )

    if not strict and parsed.borough is None and len(boroughs_with_hit) > 1:
        hits = [
            (g, c)
            for g, c in checks
            if c.number is NumberMatch.EXACT and c.street_match is StreetMatch.EXACT
        ]
        return _result(
            Outcome.AMBIGUOUS,
            "No borough was given and this address exists in "
            f"{' and '.join(sorted(boroughs_with_hit))}. Please choose one.",
            [Reason.BOROUGH_MULTIPLE],
            parsed,
            options=[_option(g, c, parsed, inferred) for g, c in hits],
            **common,
        )

    if not strict and parsed.borough is None and boroughs_with_hit and failed_boroughs:
        hits = [
            (g, c)
            for g, c in checks
            if c.number is NumberMatch.EXACT and c.street_match is StreetMatch.EXACT
        ]
        return _result(
            Outcome.AMBIGUOUS,
            f"A match was found in {', '.join(sorted(boroughs_with_hit))}, but the checks "
            f"for {', '.join(failed_boroughs)} failed, so it cannot be called unique. "
            "Please confirm the borough.",
            [Reason.BOROUGH_CHECK_INCOMPLETE],
            parsed,
            options=[_option(g, c, parsed, inferred) for g, c in hits],
            **common,
        )

    if not strict:
        if weak:
            reasons = _weak_reasons([c for _, c in weak])
            return _result(
                Outcome.AMBIGUOUS,
                "No record matches exactly, but close matches exist: "
                + "; ".join(_why_weak(c, parsed) for _, c in weak[:5])
                + ". Please confirm or correct the address.",
                reasons,
                parsed,
                options=[_option(g, c, parsed, inferred) for g, c in weak],
                **common,
            )
        return _result(
            Outcome.NO_RELIABLE_MATCH,
            _why_no_match(parsed, candidate_models),
            _no_match_reasons(parsed, candidate_models),
            parsed,
            **common,
        )

    # Exactly one strict match: cross-check its BIN with Building Footprints.
    g, c = strict[0]
    return _verify_single(g, c, parsed, inferred, socrata_client, common)


def _has_exact_in(tagged, parsed: ParsedAddress, borough: str) -> bool:
    return any(
        g.first.borough == borough
        and g.best_number(parsed) is NumberMatch.EXACT
        and g.best_street(parsed) is StreetMatch.EXACT
        for g in build_groups(tagged)
    )


def _verify_single(
    g: Group,
    c: CandidateCheck,
    parsed: ParsedAddress,
    inferred: str | None,
    socrata_client: SourceHttpClient,
    common: dict,
) -> ResolutionResult:
    borough_source = "stated" if parsed.borough else "inferred"
    reasons = [Reason.BOROUGH_STATED if parsed.borough else Reason.BOROUGH_INFERRED]
    pad_bin = c.pad_bin if c.pad_bin_status == BinStatus.ASSIGNED.value else None
    fp = None
    try:
        fp = footprints.check_bin_and_lot(socrata_client, c.bbl, pad_bin)
        common["provenance"]["requests"].append({"url": fp.url, "fetched_at": fp.fetched_at})
    except (SourceError, InvalidIdentifierError) as exc:
        common["provenance"]["footprints_error"] = str(exc)

    if c.pad_bin_status == BinStatus.UNASSIGNED_PLACEHOLDER.value:
        evidence, bin_value = BinEvidence.PLACEHOLDER, None
    elif pad_bin is None:
        evidence, bin_value = BinEvidence.MISSING, None
    elif fp is None:
        evidence, bin_value = BinEvidence.NOT_CHECKED, pad_bin
    elif not fp.lots_for_bin:
        evidence, bin_value = BinEvidence.PAD_ONLY, pad_bin
    elif c.bbl in fp.lots_for_bin:
        evidence, bin_value = BinEvidence.CROSS_VERIFIED, pad_bin
    else:
        evidence, bin_value = BinEvidence.LOT_CONFLICT, None

    identity = ResolvedIdentity(
        bbl=c.bbl,
        bin=bin_value,
        bin_evidence=evidence,
        label=c.label,
        aliases=c.aliases,
        borough=c.borough,
        borough_source=borough_source,
        footprint_bins_on_lot=fp.bins_on_lot if fp else None,
        footprints_lots_for_bin=fp.lots_for_bin if (fp and pad_bin) else None,
    )
    where = (
        f"in {c.borough} (borough you gave)"
        if parsed.borough
        else f"in {c.borough} (inferred: the only borough where PAD has this exact address)"
    )
    base = (
        f"Accepted: house number {c.house_number} and street {c.street} match exactly, "
        f"{where}; tax lot (BBL) {c.bbl}."
    )

    if evidence is BinEvidence.LOT_CONFLICT:
        return _result(
            Outcome.AMBIGUOUS,
            f"The address matches lot {c.bbl}, but official sources disagree about its building "
            f"number: PAD gives BIN {pad_bin}, while Building Footprints places that BIN on lot "
            f"{', '.join(fp.lots_for_bin)}. The BIN is left unresolved; please confirm.",
            reasons + [Reason.BIN_LOT_CONFLICT],
            parsed,
            options=[identity],
            **common,
        )

    if evidence is BinEvidence.CROSS_VERIFIED:
        level = ResolutionLevel.BUILDING
        detail = (
            f" Building number (BIN) {bin_value} is confirmed by Building Footprints "
            "on the same lot."
        )
        reasons.append(Reason.BIN_CROSS_VERIFIED)
    else:
        level = ResolutionLevel.TAX_LOT
        context = ""
        if fp and fp.bins_on_lot:
            context = (
                f" For context, Building Footprints lists {len(fp.bins_on_lot)} building(s) "
                f"on this lot ({', '.join(fp.bins_on_lot[:5])}); none is assigned to "
                "this address by us."
            )
        detail = {
            BinEvidence.PLACEHOLDER: f" PAD gives placeholder BIN {c.pad_bin} (unassigned or "
            "unknown), so the building number is unresolved." + context,
            BinEvidence.MISSING: " PAD gives no building number, so it is unresolved." + context,
            BinEvidence.PAD_ONLY: f" PAD gives BIN {bin_value}, but Building Footprints has no "
            "record of it, so it is single-source and not cross-verified.",
            BinEvidence.NOT_CHECKED: f" PAD gives BIN {bin_value}, but Building Footprints could "
            "not be reached to cross-check it.",
        }[evidence]
        detail += (
            " Verified at tax-lot level only: records keyed by building number "
            "must not be attached."
        )
        reasons.append(
            {
                BinEvidence.PLACEHOLDER: Reason.BIN_PLACEHOLDER,
                BinEvidence.MISSING: Reason.BIN_MISSING,
                BinEvidence.PAD_ONLY: Reason.BIN_PAD_ONLY,
                BinEvidence.NOT_CHECKED: Reason.FOOTPRINTS_UNAVAILABLE,
            }[evidence]
        )
    return _result(
        Outcome.VERIFIED,
        base + detail,
        reasons,
        parsed,
        resolution_level=level,
        building=identity,
        **common,
    )


def _option(
    g: Group, c: CandidateCheck, parsed: ParsedAddress, inferred: str | None
) -> ResolvedIdentity:
    assigned = c.pad_bin_status == BinStatus.ASSIGNED.value
    evidence = (
        BinEvidence.NOT_CHECKED
        if assigned
        else BinEvidence.PLACEHOLDER
        if c.pad_bin_status == BinStatus.UNASSIGNED_PLACEHOLDER.value
        else BinEvidence.MISSING
    )
    if parsed.borough and c.borough == parsed.borough:
        source = "stated"
    elif inferred and c.borough == inferred:
        source = "inferred"
    else:
        source = "source_record"
    return ResolvedIdentity(
        bbl=c.bbl,
        bin=c.pad_bin if assigned else None,
        bin_evidence=evidence,
        label=c.label,
        aliases=c.aliases,
        borough=c.borough,
        borough_source=source,
    )


def _why_weak(c: CandidateCheck, parsed: ParsedAddress) -> str:
    bits = []
    if c.number is NumberMatch.HYPHEN_ONLY:
        bits.append(
            f"house number {c.house_number} differs from {parsed.house_number} only by a hyphen"
        )
    if c.street_match is StreetMatch.NEAR:
        bits.append(f"street {c.street} is a close spelling of what you typed")
    if c.borough_match is BoroughMatch.STATED_CONFLICT:
        bits.append(f"it is in {c.borough}, not {parsed.borough}")
    if c.borough_match is BoroughMatch.UNKNOWN:
        bits.append(f"borough {c.borough} could not be confirmed")
    if c.zip_conflict:
        bits.append(f"its ZIP {c.postalcode} differs from {parsed.zip_code}")
    return f"{c.label} ({', '.join(bits) or 'needs confirmation'})"


def _weak_reasons(cands: list[CandidateCheck]) -> list[Reason]:
    out: list[Reason] = []
    for c in cands:
        if c.number is NumberMatch.HYPHEN_ONLY:
            out.append(Reason.HOUSE_NUMBER_HYPHEN)
        if c.street_match is StreetMatch.NEAR:
            out.append(Reason.STREET_TYPO)
        if c.borough_match is BoroughMatch.STATED_CONFLICT:
            out.append(Reason.BOROUGH_CONFLICT)
        if c.zip_conflict:
            out.append(Reason.ZIP_CONFLICT)
    return list(dict.fromkeys(out))


def _why_no_match(parsed: ParsedAddress, cands: list[CandidateCheck]) -> str:
    if not cands:
        return "Rejected: the City's address directory returned no NYC address for this input."
    seen = ", ".join(dict.fromkeys(c.label for c in cands[:5]))
    return (
        f"Rejected: no address record has house number {parsed.house_number} on "
        f"{parsed.street_normalized}"
        + (f" in {parsed.borough}" if parsed.borough else "")
        + f". GeoSearch's nearest results ({seen}) do not match the house number or street."
    )


def _no_match_reasons(parsed: ParsedAddress, cands: list[CandidateCheck]) -> list[Reason]:
    if not cands:
        return [Reason.NO_CANDIDATES]
    return [Reason.HOUSE_NUMBER_MISMATCH]
