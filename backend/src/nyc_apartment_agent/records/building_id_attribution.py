"""Optional, secondary attribution through HPD's BuildingID (approved decision D9).

Violation and complaint rows carry HPD's `buildingid`. When a row's own identifiers leave
it `unattributed_on_lot` (missing or placeholder BIN), that BuildingID can say which
building on the lot the row is about, but only when every condition holds:

1. The source result is a building-level join (tax-lot results are never upgraded).
2. The HPD building records for the same lot were fetched completely.
3. The row's lot membership is confirmed and it carries no conflict flags.
4. The row's BIN is missing or a placeholder (a malformed BIN is treated as contradictory).
5. The BuildingID maps to exactly one HPD building record, on the same lot, that is
   current (Building / Active), not one of several current records for its BIN, and
   whose BIN is valid and assigned.

The mapped BIN then decides `this_building` or `other_building_on_lot` exactly as a row
BIN would. The original attribution is kept on the row, the basis is named explicitly,
and the step returns before/after counts. Rows whose own BIN already decided their
attribution are never changed; a disagreement with their BuildingID is only counted.

The step returns a new result; the input result is not modified.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from ..errors import InvalidIdentifierError
from ..identifiers import BinStatus, classify_bin
from .admin_models import BuildingSourceResult, HpdBuildingRecord
from .complaint_models import ComplaintSourceResult
from .models import (
    Attribution,
    AttributionBasis,
    BuildingIdAttributionReport,
    JoinScope,
    LotMembership,
    QualityFlag,
    RetrievalStatus,
    SourceResult,
)

ROW_BLOCKING_FLAGS = {
    QualityFlag.LOT_IDENTIFIER_CONFLICT: "row_lot_identifier_conflict",
    QualityFlag.DUPLICATE_ID_DIFFERENT_CONTENT: "row_duplicate_id_conflict",
    QualityFlag.BIN_INVALID: "row_bin_malformed",
}


def _bin_status(value: str | None) -> BinStatus | None:
    try:
        return classify_bin(value)
    except InvalidIdentifierError:
        return None


def _mapping_problem(candidates: list[HpdBuildingRecord]) -> str | None:
    """Why a BuildingID cannot be used, or None when it maps cleanly."""
    if not candidates:
        return "no_hpd_building_record_on_lot"
    if len(candidates) > 1:
        return "several_hpd_building_records"
    b = candidates[0]
    if b.lot_membership is not LotMembership.CONFIRMED:
        return "hpd_building_record_not_on_lot"
    if QualityFlag.BUILDING_RECORD_MISMATCH in b.quality_flags:
        return "hpd_building_record_mismatch"
    if not b.is_current:
        return "hpd_building_record_not_current"
    if QualityFlag.MULTIPLE_RECORDS_FOR_BIN in b.quality_flags:
        return "mapped_bin_on_several_current_records"
    if _bin_status(b.source_bin) is not BinStatus.ASSIGNED:
        return "mapped_bin_missing_placeholder_or_malformed"
    return None


def _precondition_problem(result, buildings: BuildingSourceResult | None) -> str | None:
    if result.join_scope is not JoinScope.BUILDING:
        return "not_a_building_level_join"  # tax-lot results are never upgraded
    if result.status not in (RetrievalStatus.RECORDS_FOUND, RetrievalStatus.INCOMPLETE_PAGINATION):
        return f"source_status_{result.status.value}"
    if buildings is None:
        return "no_hpd_building_records"
    if buildings.status is not RetrievalStatus.RECORDS_FOUND:
        return f"hpd_building_records_{buildings.status.value}"
    if buildings.join_scope is not JoinScope.BUILDING or (
        buildings.resolved_bbl,
        buildings.resolved_bin,
    ) != (result.resolved_bbl, result.resolved_bin):
        return "hpd_building_records_for_a_different_resolution"
    return None


def _upgrade(records, resolved_bin: str, buildings: BuildingSourceResult):
    """Return (new records, report fields). Records are copied, never mutated."""
    by_id: dict[str, list[HpdBuildingRecord]] = {}
    for b in buildings.records:
        by_id.setdefault(b.building_id, []).append(b)

    out = []
    upgraded: Counter[str] = Counter()
    kept: Counter[str] = Counter()
    by_building: dict[str, dict] = {}
    disagreements = 0
    for r in records:
        if r.attribution is not Attribution.UNATTRIBUTED_ON_LOT:
            if r.building_id and r.attribution in (
                Attribution.THIS_BUILDING,
                Attribution.OTHER_BUILDING_ON_LOT,
            ):
                cands = by_id.get(r.building_id, [])
                if len(cands) == 1 and _mapping_problem(cands) is None:
                    disagreements += cands[0].source_bin != (r.source_bin or "").strip()
            out.append(r)
            continue
        reason = None
        if not r.building_id or r.building_id.strip() in ("", "0"):
            reason = "row_has_no_building_id"
        elif r.lot_membership is not LotMembership.CONFIRMED:
            reason = "row_lot_not_confirmed"
        else:
            reason = next((v for f, v in ROW_BLOCKING_FLAGS.items() if f in r.quality_flags), None)
            if reason is None and _bin_status(r.source_bin) is BinStatus.ASSIGNED:
                reason = "row_bin_assigned_but_unattributed"  # some other rule held it back
            if reason is None:
                reason = _mapping_problem(by_id.get(r.building_id, []))
        if reason is not None:
            kept[reason] += 1
            out.append(r)
            continue
        target = by_id[r.building_id][0]
        new_attr = (
            Attribution.THIS_BUILDING
            if target.source_bin == resolved_bin
            else Attribution.OTHER_BUILDING_ON_LOT
        )
        out.append(
            r.model_copy(
                update={
                    "attribution": new_attr,
                    "attribution_basis": AttributionBasis.HPD_BUILDING_ID,
                    "original_attribution": r.attribution,
                    "mapped_bin": target.source_bin,
                }
            )
        )
        upgraded[new_attr.value] += 1
        entry = by_building.setdefault(
            r.building_id,
            {"mapped_bin": target.source_bin, "attribution": new_attr.value, "rows": 0},
        )
        entry["rows"] += 1
    return out, upgraded, kept, by_building, disagreements


def _counts(records) -> dict[str, int]:
    return dict(sorted(Counter(r.attribution.value for r in records).items()))


def _skipped(result, reason: str, buildings: BuildingSourceResult | None):
    report = BuildingIdAttributionReport(
        applied=False,
        not_applied_reason=reason,
        buildings_dataset_id=buildings.dataset_id if buildings else None,
        buildings_status=buildings.status if buildings else None,
        buildings_data_as_of=buildings.provenance.data_as_of if buildings else None,
        before=_counts(result.records),
        after=_counts(result.records),
    )
    return result.model_copy(update={"building_id_attribution": report})


def apply_to_violations(
    result: SourceResult, buildings: BuildingSourceResult | None, *, as_of: dt.date
) -> SourceResult:
    from .hpd_violations import summarize  # local import: avoid a cycle at import time

    problem = _precondition_problem(result, buildings)
    if problem:
        return _skipped(result, problem, buildings)
    records, upgraded, kept, by_building, disagreements = _upgrade(
        result.records, result.resolved_bin, buildings
    )
    report = BuildingIdAttributionReport(
        applied=True,
        buildings_dataset_id=buildings.dataset_id,
        buildings_status=buildings.status,
        buildings_data_as_of=buildings.provenance.data_as_of,
        before=_counts(result.records),
        after=_counts(records),
        upgraded=dict(sorted(upgraded.items())),
        upgraded_by_building_id=dict(sorted(by_building.items())),
        kept_unattributed=dict(sorted(kept.items())),
        bin_building_id_disagreements=disagreements,
    )
    summary = summarize(records, result.join_scope, result.provenance.data_as_of, as_of)
    return SourceResult.model_validate(
        {
            **result.model_dump(exclude={"records", "summary", "building_id_attribution"}),
            "records": records,
            "summary": summary,
            "building_id_attribution": report,
        }
    )


def apply_to_complaints(
    result: ComplaintSourceResult, buildings: BuildingSourceResult | None, *, as_of: dt.date
) -> ComplaintSourceResult:
    from .hpd_complaints import group_complaints, summarize

    problem = _precondition_problem(result, buildings)
    if problem:
        return _skipped(result, problem, buildings)
    records, upgraded, kept, by_building, disagreements = _upgrade(
        result.records, result.resolved_bin, buildings
    )
    complaints = group_complaints(records)
    report = BuildingIdAttributionReport(
        applied=True,
        buildings_dataset_id=buildings.dataset_id,
        buildings_status=buildings.status,
        buildings_data_as_of=buildings.provenance.data_as_of,
        before=_counts(result.records),
        after=_counts(records),
        upgraded=dict(sorted(upgraded.items())),
        upgraded_by_building_id=dict(sorted(by_building.items())),
        kept_unattributed=dict(sorted(kept.items())),
        bin_building_id_disagreements=disagreements,
        complaints_before=_counts(result.complaints),
        complaints_after=_counts(complaints),
    )
    summary = summarize(records, complaints, result.join_scope, result.provenance.data_as_of, as_of)
    return ComplaintSourceResult.model_validate(
        {
            **result.model_dump(
                exclude={"records", "complaints", "summary", "building_id_attribution"}
            ),
            "records": records,
            "complaints": complaints,
            "summary": summary,
            "building_id_attribution": report,
        }
    )
