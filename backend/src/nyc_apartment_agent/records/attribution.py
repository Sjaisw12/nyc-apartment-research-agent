"""Decide how far one lot-level row may be read as being about the user's building.

Input: the join plan (from join_policy) and the row's identifiers.

- Lot identity first: the row's source BBL and its boroid/block/lot must not point at
  different lots. If they do, the row is `unattributed_on_lot` (approved correction 5),
  whatever the resolution level.
- Tax-lot plan: every other row is `lot_level`. The row's BIN is never consulted.
- Building plan: BIN equal to the resolved BIN -> `this_building`; another assigned BIN
  -> `other_building_on_lot`; missing, placeholder or malformed BIN -> `unattributed_on_lot`.
"""

from __future__ import annotations

from ..errors import InvalidIdentifierError
from ..identifiers import BinStatus, classify_bin, normalize_bbl
from .join_policy import LotQueryPlan, compose_bbl
from .models import Attribution, JoinScope, LotMembership, QualityFlag


def check_lot_identity(
    plan: LotQueryPlan, source_bbl: object, boroid: object, block: object, lot: object
) -> tuple[LotMembership, list[QualityFlag]]:
    flags: list[QualityFlag] = []
    raw = None if source_bbl is None else str(source_bbl).strip()
    bbl: str | None = None
    if not raw:
        flags.append(QualityFlag.BBL_MISSING)
    else:
        try:
            bbl = normalize_bbl(raw)
        except InvalidIdentifierError:
            flags.append(QualityFlag.BBL_INVALID)

    composite = compose_bbl(boroid, block, lot)
    if composite is None:
        flags.append(QualityFlag.LOT_PARTS_MISSING)

    candidates = {x for x in (bbl, composite) if x is not None}
    if len(candidates) > 1 or (candidates and plan.bbl not in candidates) or not candidates:
        # Two valid identifiers that disagree, or nothing that ties the row to our lot.
        flags.append(QualityFlag.LOT_IDENTIFIER_CONFLICT)
        return LotMembership.CONFLICTING, flags
    return LotMembership.CONFIRMED, flags


def bin_flags(source_bin: object) -> tuple[BinStatus | None, list[QualityFlag]]:
    try:
        status = classify_bin(None if source_bin is None else str(source_bin))
    except InvalidIdentifierError:
        return None, [QualityFlag.BIN_INVALID]
    if status is BinStatus.MISSING:
        return status, [QualityFlag.BIN_MISSING]
    if status is BinStatus.UNASSIGNED_PLACEHOLDER:
        return status, [QualityFlag.BIN_PLACEHOLDER]
    return status, []


def attribute(
    plan: LotQueryPlan,
    membership: LotMembership,
    bin_status: BinStatus | None,
    source_bin: object,
) -> Attribution:
    if membership is LotMembership.CONFLICTING:
        return Attribution.UNATTRIBUTED_ON_LOT
    if plan.scope is JoinScope.TAX_LOT:
        return Attribution.LOT_LEVEL
    if bin_status is not BinStatus.ASSIGNED:
        return Attribution.UNATTRIBUTED_ON_LOT
    if str(source_bin).strip() == plan.building_bin:
        return Attribution.THIS_BUILDING
    return Attribution.OTHER_BUILDING_ON_LOT
