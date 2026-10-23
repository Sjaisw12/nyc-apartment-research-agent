"""DOB ECB Violations (6bgk-3dad), Phase 2 Task 5 step 3.

Entry point: `retrieve_ecb_violations(resolution, client)`.

Rules (docs/phase2_task5_dob_311_design.md, sections 4, 6.4, 18.2, 18.3 and 18.8):

- Query: every lot in the lot family, each in both lot forms (4-digit and 5-digit,
  zero-padded) with the 5-digit block, joined by OR. At building scope `OR bin='R'` is
  added for the resolved BIN only; at tax-lot scope there is no BIN clause. Ordered by
  `isn_dob_bis_extract`. The lot family is the resolved BBL plus the `base_bbl` and
  `mappluto_bbl` that Footprints gives for the lot's BINs.
- One query, so a row satisfying both clauses comes back once; each record lists which
  clauses it satisfies. Rows are keyed by `isn_dob_bis_extract`: identical copies
  collapse (every copy kept in lineage), differing copies are all kept and fail closed.
  A repeated `ecb_violation_number` under different extract ids is never collapsed.
- Attribution, building scope: row lot in the family and BIN = R -> `this_building`;
  another BIN that Footprints or HPD places on the lot -> `other_building_on_lot`; a BIN
  in no current source -> `unattributed_on_lot` + `bin_retired_or_unknown`; a BIN that
  Footprints places on another lot -> `bin_on_other_lot`; a BIN with two-source conflict
  -> `bin_lot_conflict`; placeholder or missing BIN -> unattributed with its flag; row
  lot outside the family -> `lot_identifier_conflict`; missing lot with BIN = R ->
  `this_building`, `via_bin_mapping`, `lot_parts_missing`. A row on a family lot other
  than the resolved lot keeps its BIN attribution and is flagged `condo_lot_family`.
  Tax-lot scope: every row on the family is `lot_level`; BINs are never used to attribute.
- BINs found only on ECB rows are reported, never added to the DOB complaint search set.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources
from typing import Any

from ..config import Settings, get_settings
from ..errors import SourceError
from ..http_client import SourceHttpClient
from ..identifiers import BinStatus
from ..resolver.models import ResolutionResult
from ..sources.socrata_pager import PagedFetch, fetch_all
from .admin_models import BuildingSourceResult
from .attribution import bin_flags
from .dob_complaints import FOOTPRINTS_ID, BinPlan, _assigned, _footprints, _quote, plan_bins
from .ecb_models import (
    ECB_CONFLICT_FLAGS,
    WITHHELD_VALUE,
    Adjudication,
    CorrectionStatus,
    EcbBucket,
    EcbFinancials,
    EcbRowBin,
    EcbSourceRow,
    EcbSummary,
    EcbViolationResult,
    Infraction,
    LotFormat,
    MatchedBy,
    NormalizedEcbViolation,
    Severity,
)
from .evidence import EvidenceStore
from .hpd_buildings import retrieve_hpd_buildings
from .hpd_violations import EARLIEST_PLAUSIBLE, _as_of_date, _digest_key, _s
from .join_policy import LotQueryPlan, UnsupportedJoinError, plan_lot_query
from .models import (
    Attribution,
    AttributionBasis,
    DateValue,
    DigestBasis,
    JoinScope,
    LotMembership,
    QualityFlag,
    RawEvidence,
    RetrievalProvenance,
    RetrievalStatus,
)
from .redaction import canonical_sha256, is_sanitized, keyed_digest, needs_redaction

logger = logging.getLogger(__name__)

SOURCE = "nyc_open_data"
DATASET_ID = "6bgk-3dad"
UNIQUE_KEY = "isn_dob_bis_extract"
REQUIRED_FIELDS = (
    "isn_dob_bis_extract",
    "ecb_violation_number",
    "ecb_violation_status",
    "boro",
    "block",
    "lot",
    "bin",
    "issue_date",
    "hearing_status",
)
MAX_RESTARTS = 2  # 18.4: retry a count/data pair with different headers up to twice
# Never public (D22, D31): respondent fields, inspector free text, hearing time.
NEVER_PUBLIC = (
    "respondent_name",
    "respondent_house_number",
    "respondent_street",
    "respondent_city",
    "respondent_zip",
    "violation_description",
    "hearing_time",
)
MONEY_FIELDS = (
    ("penalty_imposed", "penality_imposed"),  # sic, as in the dataset
    ("amount_paid", "amount_paid"),
    ("balance_due", "balance_due"),
)
HEARING_DATE_HORIZON = dt.timedelta(days=5 * 366)  # hearings are scheduled ahead

_TEN = re.compile(r"^[1-5]\d{9}$")
_ISN = re.compile(r"^\d{1,12}$")
_ECB_NUMBER = re.compile(r"^(?=(?:\D*\d){6})[0-9A-Z]{6,12}$")
_DOB_NUMBER = re.compile(r"^(?=(?:\D*\d){4})[0-9A-Z /-]{4,24}$")
_MONEY = re.compile(r"^-?\d{1,12}(\.\d{1,2})?$")
_PRIVATE = re.compile(r"@|\d{3}[-. )]+\d{3}[-. ]\d{4}|\b(?:MR|MRS|MS|MISS|DR)\.?\s+[A-Z]")

STATEMENT = (
    "An ECB summons is a charge that DOB issued. It is not by itself a finding that a "
    "violation occurred. The correction status is DOB's; the hearing status is OATH's "
    "record as published by DOB, grouped by this project's mapping (not DOB's or OATH's), "
    "and the certification status is the respondent's certification with DOB. They are "
    "shown separately and never combined."
)
FINANCIAL_NOTE = (
    "Amounts as DOB recorded them for summonses attributed to the requested building. "
    "Not reconciled with OATH or DOB payment records. Negative balances are recorded by "
    "DOB; their meaning is not documented. Summonses with unparseable amounts are counted "
    "but excluded from the totals."
)
COVERAGE_NOTES = (
    "ECB summonses are searched by every lot in the lot family, in both lot formats"
    " (4-digit and 5-digit), and at building scope also by the resolved BIN.",
    "A BIN seen only on ECB rows (for example a retired BIN) is reported and never added "
    "to the DOB complaint BIN search; DOB complaints filed under such a BIN are not found.",
)


# ---- Reference data -------------------------------------------------------------------


@cache
def reference() -> dict[str, Any]:
    """Approved adjudication mapping, measured vocabularies and the infraction allowlist."""
    text = (
        resources.files("nyc_apartment_agent.records")
        .joinpath("data/ecb_reference.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


def classify_hearing(raw: str | None) -> tuple[Adjudication, str | None, str]:
    """(adjudication, public raw value, note). An unlisted value is withheld, never guessed."""
    ref = reference()
    entry = ref["adjudication"].get("None" if raw is None else raw)
    if entry is None:
        return (
            Adjudication.UNKNOWN,
            WITHHELD_VALUE,
            "The hearing status is not on the measured list; it is withheld and not grouped.",
        )
    return Adjudication(entry["adjudication"]), raw, entry["note"]


def classify_correction(raw: str | None) -> CorrectionStatus:
    value = reference()["correction_status"]["values"].get(raw or "")
    return CorrectionStatus(value) if value else CorrectionStatus.UNKNOWN


def _picklist(raw: str | None, vocabulary: str) -> tuple[str | None, bool]:
    """(public value, withheld). A value not on the measured list is withheld (18.2)."""
    if raw is None:
        return None, False
    if raw in reference()["vocabularies"][vocabulary]:
        return raw, False
    return WITHHELD_VALUE, True


def classify_severity(raw: str | None) -> tuple[Severity, str | None, list[QualityFlag]]:
    if raw is None:
        return Severity.UNKNOWN, None, []
    entry = reference()["severity"].get(raw)
    if entry is None:
        return Severity.UNKNOWN, WITHHELD_VALUE, [QualityFlag.UNCOMMON_VALUE_WITHHELD]
    sev = Severity(entry["severity"])
    return sev, raw, ([QualityFlag.SEVERITY_UNDOCUMENTED_VALUE] if not entry["documented"] else [])


def infractions(row: dict[str, Any]) -> tuple[list[Infraction], bool]:
    """Codes on the measured list; texts only for allowlisted (code, text) pairs."""
    ref = reference()
    codes, texts = set(ref["infraction_codes"]), ref["infraction_texts"]
    out: list[Infraction] = []
    withheld = False
    for n in range(1, 11):
        code = _s(row.get(f"infraction_code{n}"))
        raw_text = row.get(f"section_law_description{n}")
        text = None if raw_text is None or str(raw_text).strip() == "" else str(raw_text)
        if code is None and text is None:
            continue
        public_code = code if code in codes else None
        shown = (
            text
            if (public_code is not None and text is not None and texts.get(code) == text)
            else None
        )
        text_withheld = text is not None and shown is None
        withheld = withheld or text_withheld or (code is not None and public_code is None)
        out.append(
            Infraction(
                position=n, code=public_code, law_description=shown, text_withheld=text_withheld
            )
        )
    return out, withheld


# ---- Values ---------------------------------------------------------------------------


def parse_ecb_date(
    raw: object, *, as_of: dt.date, horizon: dt.timedelta = dt.timedelta(days=1)
) -> tuple[DateValue, QualityFlag | None]:
    """ECB writes YYYYMMDD. A malformed value's raw text is not shown (it is free text)."""
    if raw is None or str(raw).strip() == "":
        return DateValue(raw=None, value=None), None
    text = str(raw).strip()
    if not re.fullmatch(r"\d{8}", text):
        return DateValue(raw=None, value=None), QualityFlag.UNPARSEABLE_DATE
    try:
        value = dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        return DateValue(raw=text, value=None), QualityFlag.UNPARSEABLE_DATE
    if value < EARLIEST_PLAUSIBLE or value > as_of + horizon:
        return DateValue(raw=text, value=None), QualityFlag.IMPLAUSIBLE_DATE
    return DateValue(raw=text, value=value), None


def parse_money(raw: object) -> tuple[Decimal | None, bool]:
    """(amount, unparseable). Plain decimal numbers only; never a float."""
    if raw is None or str(raw).strip() == "":
        return None, False
    text = str(raw).strip()
    if not _MONEY.match(text):
        return None, True
    try:
        return Decimal(text), False
    except InvalidOperation:  # pragma: no cover - the pattern already excludes this
        return None, True


def parse_lot(row: dict[str, Any]) -> tuple[str | None, LotFormat]:
    """(row BBL, lot format). Boro 1-5, a 5-digit block, a 4- or 5-digit lot."""
    boro, block, lot = (_s(row.get(f)) for f in ("boro", "block", "lot"))
    if lot is None:
        return None, LotFormat.MISSING
    if re.fullmatch(r"\d{4}", lot):
        lot4, fmt = lot, LotFormat.FOUR_DIGIT
    elif re.fullmatch(r"0\d{4}", lot):
        lot4, fmt = lot[1:], LotFormat.FIVE_DIGIT
    else:
        return None, LotFormat.MALFORMED
    if boro is None or block is None:
        return None, LotFormat.MISSING
    if not (re.fullmatch(r"[1-5]", boro) and re.fullmatch(r"\d{5}", block)):
        return None, LotFormat.MALFORMED
    if lot4 == "0000" or block == "00000":
        return None, LotFormat.MALFORMED
    return f"{boro}{block}{lot4}", fmt


def lot_clause(family: list[str]) -> str:
    """The recorded query form: each family lot in both formats, 5-digit block."""
    parts = []
    for bbl in family:
        if not _TEN.match(bbl):
            raise ValueError(f"refusing to quote a non-BBL value: {bbl!r}")
        boro, block, lot4 = bbl[0], bbl[1:6], bbl[6:]
        parts.append(f"(boro='{boro}' AND block='{block}' AND lot in ('{lot4}', '0{lot4}'))")
    return " OR ".join(parts)


def build_where(family: list[str], resolved_bin: str | None) -> str:
    where = lot_clause(family)
    if resolved_bin is not None:
        where += f" OR bin={_quote([resolved_bin])}"
    return where


# ---- Lot family and BIN evidence ------------------------------------------------------


@dataclass
class _Lot:
    family: list[str]
    lot_bins: set[str] = field(default_factory=set)  # BINs Footprints places on the lot
    plan: BinPlan | None = None  # building scope only
    provenance: list[RetrievalProvenance] = field(default_factory=list)
    incomplete_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)  # attribution only


def _family_values(values: set[str], out: _Lot) -> list[str]:
    good = sorted(v for v in values if _TEN.match(v))
    bad = sorted(values - set(good))
    if bad:
        out.warnings.append(f"lot_family_value_ignored: {len(bad)} value(s) not a BBL")
    return good


def _tax_lot_family(client: SourceHttpClient, plan: LotQueryPlan, page_size: int) -> _Lot:
    """Footprints by lot only (no BIN lookup at tax-lot scope)."""
    out = _Lot(family=[plan.bbl])
    family = {plan.bbl}
    try:
        fp = _footprints(client, f"base_bbl='{plan.bbl}' OR mappluto_bbl='{plan.bbl}'", page_size)
        out.provenance.append(fp.provenance)
        if not fp.complete:
            out.incomplete_reasons.append(
                "lot_family_incomplete: footprints lot lookup "
                + "; ".join(fp.provenance.incomplete_reasons)
            )
        for row in fp.rows:
            for f in ("base_bbl", "mappluto_bbl"):
                if _s(row.get(f)):
                    family.add(str(row[f]).strip())
            b = _assigned(row.get("bin"))
            if b:
                out.lot_bins.add(b)
    except SourceError as exc:
        out.provenance.append(
            RetrievalProvenance(dataset_id=FOOTPRINTS_ID, error=type(exc).__name__)
        )
        out.incomplete_reasons.append("lot_family_incomplete: footprints unavailable")
    out.family = _family_values(family, out)
    return out


def _building_family(
    client: SourceHttpClient, plan: LotQueryPlan, hpd: BuildingSourceResult, page_size: int
) -> _Lot:
    bins = plan_bins(client, plan, hpd, page_size=page_size)
    out = _Lot(family=[], plan=bins, provenance=list(bins.provenance))
    out.lot_bins = {
        b
        for b, ev in bins.evidence.items()
        if b not in bins.conflicts and {s.value for s in ev.sources} & {"footprints", "hpd"}
    }
    # Only a failed Footprints lot lookup can make the lot family (and so the ECB query)
    # incomplete. Other gaps affect attribution only, and those rows fail closed.
    for r in bins.incomplete_reasons:
        family_gap = r.startswith("bin_evidence_incomplete: footprints lot lookup") or (
            r == "bin_evidence_incomplete: footprints unavailable"
        )
        if family_gap:
            out.incomplete_reasons.append(
                r.replace("bin_evidence_incomplete", "lot_family_incomplete", 1)
            )
        else:
            out.evidence_gaps.append(r)
    out.family = _family_values(set(bins.lot_family) | {plan.bbl}, out)
    return out


def _lookup_row_bins(
    client: SourceHttpClient, bins: list[str], page_size: int, lot: _Lot
) -> dict[str, set[str]] | None:
    """Footprints lots for BINs seen only on ECB rows (one query). None if it failed."""
    if not bins:
        return {}
    try:
        fb = _footprints(client, f"bin in ({_quote(bins)})", page_size)
    except SourceError as exc:
        lot.provenance.append(
            RetrievalProvenance(dataset_id=FOOTPRINTS_ID, error=type(exc).__name__)
        )
        return None
    lot.provenance.append(fb.provenance)
    if not fb.complete:
        return None
    lots: dict[str, set[str]] = {b: set() for b in bins}
    for row in fb.rows:
        b = _s(row.get("bin"))
        if b in lots:
            lots[b].update(
                str(row[f]).strip() for f in ("base_bbl", "mappluto_bbl") if _s(row.get(f))
            )
    return lots


# ---- Rows ---------------------------------------------------------------------------------


@dataclass
class _Ctx:
    plan: LotQueryPlan
    lot: _Lot
    row_bin_lots: dict[str, set[str]] | None  # by-BIN Footprints lookup, building scope
    as_of: dt.date
    digest_key: bytes
    data_as_of: str | None
    evidence: EvidenceStore | None


def _attribute(ctx: _Ctx, row_bbl: str | None, bin_value: str | None):
    """(attribution, lot membership, flags) per the decision table in design 18.8."""
    flags: list[QualityFlag] = []
    status, b_flags = bin_flags(bin_value)
    assigned = status is BinStatus.ASSIGNED
    b = str(bin_value).strip() if assigned else None
    family = set(ctx.lot.family)
    building = ctx.plan.scope is JoinScope.BUILDING
    R = ctx.plan.building_bin
    conflicts = set(ctx.lot.plan.conflicts) if ctx.lot.plan else set()

    if row_bbl is None:  # missing or malformed lot parts
        flags.append(QualityFlag.LOT_PARTS_MISSING)
        flags += b_flags
        if building and b == R:
            if R in conflicts:
                flags.append(QualityFlag.BIN_LOT_CONFLICT)
                return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags
            return Attribution.THIS_BUILDING, LotMembership.VIA_BIN_MAPPING, flags
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags

    if row_bbl not in family:
        flags.append(QualityFlag.LOT_IDENTIFIER_CONFLICT)
        flags += b_flags
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags

    if row_bbl != ctx.plan.bbl:
        flags.append(QualityFlag.CONDO_LOT_FAMILY)
    flags += b_flags
    if not building:  # tax lot: the BIN never attributes; unknown BINs only noted
        if b is not None and b not in ctx.lot.lot_bins:
            flags.append(QualityFlag.BIN_NOT_ON_CURRENT_LOT)
        return Attribution.LOT_LEVEL, LotMembership.CONFIRMED, flags
    if b is None:
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFIRMED, flags
    if b in conflicts:
        flags.append(QualityFlag.BIN_LOT_CONFLICT)
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags
    ev = ctx.lot.plan.evidence.get(b) if ctx.lot.plan else None
    if b == R:
        flags += [f for f in (ev.flags if ev else []) if f not in flags]
        return Attribution.THIS_BUILDING, LotMembership.CONFIRMED, flags
    if b in ctx.lot.lot_bins:
        flags += [f for f in (ev.flags if ev else []) if f not in flags]
        return Attribution.OTHER_BUILDING_ON_LOT, LotMembership.CONFIRMED, flags
    # A BIN on no current list for the lot: classify from the by-BIN lookup.
    lots = None if ctx.row_bin_lots is None else ctx.row_bin_lots.get(b)
    if lots is None:
        flags.append(QualityFlag.BIN_NOT_ON_CURRENT_LOT)
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFIRMED, flags
    if not lots:
        flags.append(QualityFlag.BIN_RETIRED_OR_UNKNOWN)
        return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFIRMED, flags
    if lots & family:  # Footprints does place it on the lot family; row lot agrees
        flags.append(QualityFlag.BIN_SINGLE_SOURCE)
        return Attribution.OTHER_BUILDING_ON_LOT, LotMembership.CONFIRMED, flags
    flags.append(QualityFlag.BIN_ON_OTHER_LOT)
    return Attribution.UNATTRIBUTED_ON_LOT, LotMembership.CONFLICTING, flags


def _identifier(raw: str | None, pattern: re.Pattern[str]) -> str | None:
    if raw is None or not pattern.match(raw) or needs_redaction(raw) or _PRIVATE.search(raw):
        return None
    return raw


def normalize_row(
    row: dict[str, Any], ctx: _Ctx, *, row_index: int, query_url: str, fetched_at: str
) -> NormalizedEcbViolation:
    isn = _s(row.get(UNIQUE_KEY)) or "(none)"
    row_bbl, lot_format = parse_lot(row)
    bin_value = _s(row.get("bin"))
    attribution, membership, flags = _attribute(ctx, row_bbl, bin_value)
    withheld: list[str] = [f for f in NEVER_PUBLIC if f in row]

    matched: list[MatchedBy] = []
    if row_bbl is not None and row_bbl in ctx.lot.family:
        matched.append(MatchedBy.LOT)
    if ctx.plan.scope is JoinScope.BUILDING and bin_value == ctx.plan.building_bin:
        matched.append(MatchedBy.RESOLVED_BIN)

    def ident(field_name: str, pattern: re.Pattern[str]) -> str | None:
        raw = _s(row.get(field_name))
        value = _identifier(raw, pattern)
        if raw is not None and value is None:
            withheld.append(field_name)
            if QualityFlag.IDENTIFIER_WITHHELD not in flags:
                flags.append(QualityFlag.IDENTIFIER_WITHHELD)
        return value

    ecb_number = ident("ecb_violation_number", _ECB_NUMBER)
    dob_number = ident("dob_violation_number", _DOB_NUMBER)
    if not _ISN.match(isn):
        raise ValueError("isn_dob_bis_extract is not numeric")  # the pager's unique key

    def pick(field_name: str, vocabulary: str) -> str | None:
        value, was_withheld = _picklist(_s(row.get(field_name)), vocabulary)
        if was_withheld:
            withheld.append(field_name)
            if QualityFlag.UNCOMMON_VALUE_WITHHELD not in flags:
                flags.append(QualityFlag.UNCOMMON_VALUE_WITHHELD)
        return value

    correction_raw = pick("ecb_violation_status", "ecb_violation_status")
    correction = classify_correction(_s(row.get("ecb_violation_status")))
    if correction is CorrectionStatus.UNKNOWN:
        flags.append(QualityFlag.STATUS_UNKNOWN)
    adjudication, hearing_raw, note = classify_hearing(_s(row.get("hearing_status")))
    if hearing_raw == WITHHELD_VALUE:
        withheld.append("hearing_status")
        if QualityFlag.UNCOMMON_VALUE_WITHHELD not in flags:
            flags.append(QualityFlag.UNCOMMON_VALUE_WITHHELD)
    if adjudication is Adjudication.UNKNOWN and QualityFlag.STATUS_UNKNOWN not in flags:
        flags.append(QualityFlag.STATUS_UNKNOWN)
    if (
        correction is CorrectionStatus.OPEN_WITH_DOB and adjudication is Adjudication.DISMISSED
    ) or (
        correction is CorrectionStatus.RESOLVED_WITH_DOB and adjudication is Adjudication.PENDING
    ):
        flags.append(QualityFlag.STATUS_FIELDS_DISAGREE)

    severity, severity_raw, sev_flags = classify_severity(_s(row.get("severity")))
    if severity_raw == WITHHELD_VALUE:
        withheld.append("severity")
    flags += [f for f in sev_flags if f not in flags]

    infs, inf_withheld = infractions(row)
    if inf_withheld:
        withheld.append("infractions")
        if QualityFlag.UNCOMMON_VALUE_WITHHELD not in flags:
            flags.append(QualityFlag.UNCOMMON_VALUE_WITHHELD)

    dates: dict[str, DateValue] = {}
    for name in ("issue_date", "served_date", "hearing_date"):
        horizon = HEARING_DATE_HORIZON if name == "hearing_date" else dt.timedelta(days=1)
        value, flag = parse_ecb_date(row.get(name), as_of=ctx.as_of, horizon=horizon)
        dates[name] = value
        if flag and flag not in flags:
            flags.append(flag)
    issued = dates["issue_date"].value
    for name in ("served_date", "hearing_date"):
        other = dates[name].value
        if issued and other and other < issued and QualityFlag.DATE_ORDER not in flags:
            flags.append(QualityFlag.DATE_ORDER)

    money: dict[str, Decimal | None] = {}
    for public, source in MONEY_FIELDS:
        amount, bad = parse_money(row.get(source))
        money[public] = amount
        if bad:
            withheld.append(source)
            if QualityFlag.AMOUNT_UNPARSEABLE not in flags:
                flags.append(QualityFlag.AMOUNT_UNPARSEABLE)

    # Public copy of the row for the redacted hash: withheld fields replaced.
    public_row = {k: ("[WITHHELD]" if k in withheld else v) for k, v in row.items()}
    for inf in infs:
        if inf.text_withheld:
            public_row[f"section_law_description{inf.position}"] = "[WITHHELD]"
        if inf.code is None:
            public_row.pop(f"infraction_code{inf.position}", None)
    original_sha = canonical_sha256(row)
    original_digest = keyed_digest(row, ctx.digest_key)
    redacted_sha = canonical_sha256(public_row)
    basis = DigestBasis.SANITIZED_ROW if is_sanitized(row) else DigestBasis.ORIGINAL_ROW
    if ctx.evidence is not None:
        ctx.evidence.put(
            RawEvidence(
                dataset_id=DATASET_ID,
                source_record_id=isn,
                original_sha256=original_sha,
                original_digest=original_digest,
                basis=basis,
                redacted_sha256=redacted_sha,
                redacted_fields=sorted(set(withheld)),
                original_row=row,
                query_url=query_url,
                fetched_at=fetched_at,
                data_as_of=ctx.data_as_of,
            )
        )

    return NormalizedEcbViolation(
        dataset_id=DATASET_ID,
        isn_dob_bis_extract=isn,
        ecb_violation_number=ecb_number,
        dob_violation_number=dob_number,
        source_rows=[
            EcbSourceRow(
                source_row_id=isn,
                original_digest=original_digest,
                row_index=row_index,
                query_url=query_url,
                fetched_at=fetched_at,
            )
        ],
        matched_by=matched,
        source_bin=bin_value if bin_value is None or re.fullmatch(r"\d{7}", bin_value) else None,
        source_boro=_s(row.get("boro")) if re.fullmatch(r"\d", _s(row.get("boro")) or "") else None,
        source_block=_s(row.get("block"))
        if re.fullmatch(r"\d{1,5}", _s(row.get("block")) or "")
        else None,
        source_lot=_s(row.get("lot"))
        if re.fullmatch(r"\d{1,5}", _s(row.get("lot")) or "")
        else None,
        lot_format=lot_format,
        row_bbl=row_bbl,
        resolved_bbl=ctx.plan.bbl,
        lot_membership=membership,
        attribution=attribution,
        attribution_basis=AttributionBasis.ROW_IDENTIFIERS,
        correction_status=correction,
        correction_status_raw=correction_raw,
        adjudication=adjudication,
        hearing_status_raw=hearing_raw,
        adjudication_note=note,
        certification_status=pick("certification_status", "certification_status"),
        severity=severity,
        severity_raw=severity_raw,
        violation_type=pick("violation_type", "violation_type"),
        aggravated_level=pick("aggravated_level", "aggravated_level"),
        infractions=infs,
        issue_date=dates["issue_date"],
        served_date=dates["served_date"],
        hearing_date=dates["hearing_date"],
        penalty_imposed=money["penalty_imposed"],
        amount_paid=money["amount_paid"],
        balance_due=money["balance_due"],
        withheld_fields=sorted(set(withheld)),
        quality_flags=flags,
        original_digest=original_digest,
        digest_basis=basis,
        redacted_sha256=redacted_sha,
        query_url=query_url,
        fetched_at=fetched_at,
        data_as_of=ctx.data_as_of,
    )


def normalize_fetch(fetch: PagedFetch, ctx: _Ctx) -> list[NormalizedEcbViolation]:
    out: list[NormalizedEcbViolation] = []
    seen: dict[str, dict[str, int]] = {}  # isn -> {content sha: index}
    pages = {p.index: p for p in fetch.provenance.pages}
    for i, row in enumerate(fetch.rows):
        page = pages.get(fetch.row_page[i] if i < len(fetch.row_page) else -1)
        isn = _s(row.get(UNIQUE_KEY)) or "(none)"
        sha = canonical_sha256(row)
        copies = seen.setdefault(isn, {})
        record = normalize_row(
            row,
            ctx,
            row_index=i,
            query_url=page.url if page else "",
            fetched_at=page.fetched_at if page else "",
        )
        if sha in copies:
            kept = out[copies[sha]]
            kept.source_rows.extend(record.source_rows)
            if QualityFlag.DUPLICATE_ID_SAME_CONTENT not in kept.quality_flags:
                kept.quality_flags.append(QualityFlag.DUPLICATE_ID_SAME_CONTENT)
            continue
        copies[sha] = len(out)
        out.append(record)
    differing = {i for c in seen.values() if len(c) > 1 for i in c.values()}
    # A repeated summons number under different extract ids: keep all, fail closed.
    by_number: dict[str, list[int]] = {}
    for i, r in enumerate(out):
        if r.ecb_violation_number:
            by_number.setdefault(r.ecb_violation_number, []).append(i)
    differing |= {i for idx in by_number.values() if len(idx) > 1 for i in idx}
    for i in sorted(differing):
        rec = out[i]
        flags = list(rec.quality_flags)
        if QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT not in flags:
            flags.append(QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT)
        update: dict[str, Any] = {"quality_flags": flags}
        if rec.attribution in (Attribution.THIS_BUILDING, Attribution.OTHER_BUILDING_ON_LOT):
            update["attribution"] = Attribution.UNATTRIBUTED_ON_LOT
            if rec.lot_membership is LotMembership.VIA_BIN_MAPPING:
                update["lot_membership"] = LotMembership.CONFLICTING
        out[i] = NormalizedEcbViolation.model_validate({**rec.model_dump(), **update})
    return out


def row_bins_not_on_lot(records: list[NormalizedEcbViolation], ctx: _Ctx) -> list[EcbRowBin]:
    counts: Counter[str] = Counter()
    for r in records:
        for f in (
            QualityFlag.BIN_RETIRED_OR_UNKNOWN,
            QualityFlag.BIN_ON_OTHER_LOT,
            QualityFlag.BIN_NOT_ON_CURRENT_LOT,
        ):
            if f in r.quality_flags and r.source_bin:
                counts[r.source_bin] += 1
                break
        else:
            if (
                QualityFlag.BIN_SINGLE_SOURCE in r.quality_flags
                and r.source_bin
                and r.source_bin not in ctx.lot.lot_bins
            ):
                counts[r.source_bin] += 1
    out = []
    for b, n in sorted(counts.items()):
        lots = None if ctx.row_bin_lots is None else ctx.row_bin_lots.get(b)
        if ctx.plan.scope is not JoinScope.BUILDING or lots is None:
            kind, note = (
                "not_checked",
                (
                    "on no current Footprints list for this lot; not looked up by BIN at "
                    "tax-lot scope"
                    if ctx.plan.scope is not JoinScope.BUILDING
                    else "on no current list for this lot; the Footprints lookup by BIN failed"
                ),
            )
        elif not lots:
            kind, note = "retired_or_unknown", "in no current Footprints or HPD record"
        elif lots & set(ctx.lot.family):
            kind, note = "on_lot_family", "Footprints places it on the lot family"
        else:
            kind, note = "on_other_lot", "Footprints places it on another lot"
        out.append(
            EcbRowBin(
                bin=b,
                rows=n,
                classification=kind,
                footprints_lots_for_bin=sorted(lots or ()),
                note=note + "; not added to the DOB complaint BIN search",
            )
        )
    return out


# ---- Summary ------------------------------------------------------------------------------


def _inc(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1


def financials(records: list[NormalizedEcbViolation]) -> EcbFinancials:
    mine = [r for r in records if r.attribution is Attribution.THIS_BUILDING]
    totals = {"penalty_imposed": Decimal(0), "amount_paid": Decimal(0), "balance_due": Decimal(0)}
    unparseable = positive = negative = 0
    for r in mine:
        if QualityFlag.AMOUNT_UNPARSEABLE in r.quality_flags:
            unparseable += 1
            continue
        for k in totals:
            v = getattr(r, k)
            if v is not None:
                totals[k] += v
        if r.balance_due is not None:
            positive += r.balance_due > 0
            negative += r.balance_due < 0
    return EcbFinancials(
        summonses=len(mine),
        penalty_imposed_total=totals["penalty_imposed"],
        amount_paid_total=totals["amount_paid"],
        balance_due_total=totals["balance_due"],
        summonses_with_positive_balance=positive,
        summonses_with_negative_balance=negative,
        summonses_with_unparseable_amounts=unparseable,
        note=FINANCIAL_NOTE,
    )


def summarize(
    records: list[NormalizedEcbViolation],
    scope: JoinScope,
    data_as_of: str | None,
    as_of: dt.date,
    source_rows: int,
    *,
    resolved_bin_conflict: bool = False,
) -> EcbSummary:
    buckets: dict[Attribution, EcbBucket] = {}
    flags: Counter[str] = Counter()
    lot_formats: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    notes: dict[str, str] = {}
    for r in records:
        b = buckets.setdefault(r.attribution, EcbBucket())
        b.summonses += 1
        _inc(b.by_correction_status, r.correction_status.value)
        _inc(b.by_adjudication, r.adjudication.value)
        _inc(
            b.by_correction_and_adjudication, f"{r.correction_status.value}|{r.adjudication.value}"
        )
        _inc(b.by_certification_status, r.certification_status or "none")
        _inc(b.by_severity, r.severity.value)
        _inc(b.by_violation_type, r.violation_type or "none")
        _inc(b.by_bin, r.source_bin or "none")
        _inc(b.by_lot_format, r.lot_format.value)
        if r.issue_date.value:
            age = (as_of - r.issue_date.value).days
            b.issued_last_12_months += age <= 365
            b.issued_last_36_months += age <= 3 * 365
        flags.update(f.value for f in r.quality_flags)
        lot_formats[r.lot_format.value] += 1
        matched["+".join(m.value for m in r.matched_by) or "none"] += 1
        notes[r.hearing_status_raw or "None"] = r.adjudication_note
    for b in buckets.values():
        for name in (
            "by_correction_status",
            "by_adjudication",
            "by_correction_and_adjudication",
            "by_certification_status",
            "by_severity",
            "by_violation_type",
            "by_bin",
            "by_lot_format",
        ):
            d = getattr(b, name)
            setattr(b, name, dict(sorted(d.items())))
    allowed = scope is JoinScope.BUILDING and not resolved_bin_conflict
    return EcbSummary(
        data_as_of=data_as_of,
        join_scope=scope,
        building_specific_claims_allowed=allowed,
        building_claims_denied_reason=(
            "the resolved BIN has conflicting lot evidence (bin_lot_conflict); no summons is "
            "attributed to the requested building"
            if scope is JoinScope.BUILDING and resolved_bin_conflict
            else None
        ),
        total_summonses=len(records),
        source_rows=source_rows,
        by_attribution=dict(sorted(buckets.items())),
        lot_format_counts=dict(sorted(lot_formats.items())),
        matched_by_counts=dict(sorted(matched.items())),
        status_fields_disagree=flags.get(QualityFlag.STATUS_FIELDS_DISAGREE.value, 0),
        adjudication_notes=dict(sorted(notes.items())),
        requested_building_financials=financials(records) if allowed else None,
        quality_flag_counts=dict(sorted(flags.items())),
        conflict_counts={f.value: flags.get(f.value, 0) for f in sorted(ECB_CONFLICT_FLAGS)},
        statement=STATEMENT,
    )


# ---- Entry point --------------------------------------------------------------------------


def _result(**kw) -> EcbViolationResult:
    return EcbViolationResult(source=SOURCE, dataset_id=DATASET_ID, **kw)


def _row_bins_to_look_up(fetch: PagedFetch, lot: _Lot) -> list[str]:
    """Assigned BINs on family-lot rows that are on no current list for the lot."""
    known = set(lot.lot_bins) | set(lot.plan.evidence if lot.plan else ())
    family = set(lot.family)
    out = set()
    for row in fetch.rows:
        bbl, _ = parse_lot(row)
        b = _assigned(row.get("bin"))
        if b and bbl in family and b not in known:
            out.add(b)
    return sorted(out)


def retrieve_ecb_violations(
    resolution: ResolutionResult,
    client: SourceHttpClient,
    *,
    settings: Settings | None = None,
    hpd_buildings: BuildingSourceResult | None = None,
    evidence: EvidenceStore | None = None,
    digest_key: bytes | None = None,
    page_size: int | None = None,
    row_cap: int | None = None,
    today: dt.date | None = None,
) -> EcbViolationResult:
    """ECB summonses for a resolved lot (building or tax-lot scope).

    `hpd_buildings` may pass a Task 3 result already retrieved for this resolution
    (building scope only; it is fetched here otherwise).
    """
    settings = settings or get_settings()
    page_size = page_size or settings.socrata_page_size
    row_cap = row_cap or settings.source_row_cap

    try:
        plan = plan_lot_query(resolution)
    except UnsupportedJoinError as exc:
        return _result(
            status=RetrievalStatus.UNSUPPORTED_JOIN,
            status_detail=str(exc),
            join_scope=None,
            resolved_bbl=None,
            resolved_bin=None,
            provenance=RetrievalProvenance(dataset_id=DATASET_ID),
        )

    building = plan.scope is JoinScope.BUILDING
    if building:
        if hpd_buildings is None:
            hpd_buildings = retrieve_hpd_buildings(
                resolution, client, settings=settings, page_size=page_size, today=today
            )
        elif hpd_buildings.resolved_bbl != plan.bbl:
            raise ValueError("hpd_buildings was retrieved for a different lot")
        lot = _building_family(client, plan, hpd_buildings, page_size)
    else:
        lot = _tax_lot_family(client, plan, page_size)

    resolved_bin = plan.building_bin if building else None
    where = build_where(lot.family, resolved_bin)
    formats = [f"{bbl}:{lot}" for bbl in lot.family for lot in (bbl[6:], "0" + bbl[6:])]

    def common() -> dict[str, Any]:
        bins = lot.plan
        return {
            "join_scope": plan.scope,
            "resolved_bbl": plan.bbl,
            "resolved_bin": resolved_bin,
            "lot_family": lot.family,
            "lot_formats_queried": formats,
            "bin_clause": resolved_bin,
            "bin_evidence": sorted(bins.evidence.values(), key=lambda e: e.bin) if bins else [],
            "bin_lot_conflicts": sorted(bins.conflicts) if bins else [],
            "bin_evidence_provenance": lot.provenance,
            "bin_evidence_complete": not (lot.incomplete_reasons or lot.evidence_gaps),
            "coverage_notes": list(COVERAGE_NOTES),
        }

    try:
        fetch = fetch_all(
            client,
            DATASET_ID,
            where=where,
            order_by=UNIQUE_KEY,
            unique_key=UNIQUE_KEY,
            page_size=page_size,
            row_cap=row_cap,
            required_fields=REQUIRED_FIELDS,
            max_restarts=MAX_RESTARTS,
        )
    except SourceError as exc:
        logger.warning("%s unavailable: %s", DATASET_ID, type(exc).__name__)
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"{type(exc).__name__}: the source could not be checked",
            provenance=RetrievalProvenance(
                dataset_id=DATASET_ID, where=where, order_by=UNIQUE_KEY, error=type(exc).__name__
            ),
            **common(),
        )

    prov = fetch.provenance
    missing = [w for w in prov.warnings if w.startswith("fields_absent_from_all_rows")]
    if missing:
        return _result(
            status=RetrievalStatus.SOURCE_UNAVAILABLE,
            status_detail=f"schema_changed: {missing[0]}",
            provenance=prov,
            **common(),
        )

    row_bin_lots: dict[str, set[str]] | None = {}
    if building:
        lookup = _row_bins_to_look_up(fetch, lot)
        row_bin_lots = _lookup_row_bins(client, lookup, page_size, lot)
        if row_bin_lots is None:
            prov.warnings.append(
                f"row_bin_lookup_failed: {len(lookup)} BIN(s) seen only on ECB rows could not "
                "be checked in Footprints; their rows stay unattributed"
            )
    prov.warnings.extend(lot.warnings)
    prov.warnings.extend(f"attribution_evidence_gap: {g}" for g in lot.evidence_gaps)

    as_of = _as_of_date(fetch, today)
    if digest_key is None:
        digest_key = _digest_key(settings, fetch)
    ctx = _Ctx(plan, lot, row_bin_lots, as_of, digest_key, prov.data_as_of, evidence)
    records = normalize_fetch(fetch, ctx)
    prov.incomplete_reasons = prov.incomplete_reasons + lot.incomplete_reasons
    if prov.incomplete_reasons:
        status = RetrievalStatus.INCOMPLETE_PAGINATION
        detail = "; ".join(prov.incomplete_reasons)
    elif prov.source_count == 0:
        status = RetrievalStatus.NO_RECORDS_FOUND
        detail = f"the source reports 0 ECB summonses for {len(lot.family)} lot(s)"
    else:
        status = RetrievalStatus.RECORDS_FOUND
        detail = (
            f"{len(records)} summonses from {len(fetch.rows)} source rows for "
            f"{len(lot.family)} lot(s); matches the source count"
        )
    resolved_conflict = bool(lot.plan and plan.building_bin in lot.plan.conflicts)
    return _result(
        status=status,
        status_detail=detail,
        records=records,
        partial=status is RetrievalStatus.INCOMPLETE_PAGINATION,
        summary=summarize(
            records,
            plan.scope,
            prov.data_as_of,
            as_of,
            len(fetch.rows),
            resolved_bin_conflict=resolved_conflict,
        ),
        provenance=prov,
        row_bins_not_on_lot=row_bins_not_on_lot(records, ctx),
        **common(),
    )
