"""Which records may be attached to a resolution, and how.

This is the single place that reads Phase 1's `permitted_join_keys`. Every record
adapter calls it before making any request.

Rules:
- No permitted keys (ambiguous, no match, source unavailable): no query at all.
- `bbl` permitted: the lot may be queried by its BBL, and by boroid + block + lot,
  which is the same identifier split into parts (approved decision D1).
- `bin` permitted (building-level resolution with a cross-verified BIN): the BIN may be
  used to sort lot rows into this building / other buildings. Lot-keyed sources are
  still fetched by lot, never by BIN.
- Sources keyed only by BIN must call `require_bin_join`, which refuses tax-lot results.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..identifiers import BinStatus, classify_bin, normalize_bbl
from ..resolver.models import Outcome, ResolutionLevel, ResolutionResult
from .models import JoinScope


class UnsupportedJoinError(Exception):
    """The resolution does not permit the requested join. No request may be made."""


@dataclass(frozen=True)
class LotQueryPlan:
    bbl: str
    boroid: int
    block: int
    lot: int
    scope: JoinScope
    building_bin: str | None  # set only at building scope


def split_bbl(bbl: str) -> tuple[int, int, int]:
    bbl = normalize_bbl(bbl)
    return int(bbl[0]), int(bbl[1:6]), int(bbl[6:])


def compose_bbl(boroid: object, block: object, lot: object) -> str | None:
    """Rebuild a BBL from parts. Returns None unless every part is a valid integer."""
    try:
        b, blk, lt = int(str(boroid)), int(str(block)), int(str(lot))
    except (TypeError, ValueError):
        return None
    if not (1 <= b <= 5 and 1 <= blk <= 99999 and 1 <= lt <= 9999):
        return None
    return f"{b}{blk:05d}{lt:04d}"


def plan_lot_query(resolution: ResolutionResult) -> LotQueryPlan:
    keys = resolution.permitted_join_keys
    if not keys or resolution.outcome is not Outcome.VERIFIED or resolution.building is None:
        raise UnsupportedJoinError(
            f"resolution outcome is {resolution.outcome.value}; no records may be attached"
        )
    if "bbl" not in keys:
        raise UnsupportedJoinError("resolution does not permit a BBL join")

    bbl = normalize_bbl(resolution.building.bbl)
    boroid, block, lot = split_bbl(bbl)

    building_bin: str | None = None
    scope = JoinScope.TAX_LOT
    if "bin" in keys:
        # Belt and braces: Phase 1 only grants "bin" at building level with an assigned BIN.
        if resolution.resolution_level is not ResolutionLevel.BUILDING:
            raise UnsupportedJoinError("BIN join offered without a building-level resolution")
        if classify_bin(resolution.building.bin) is not BinStatus.ASSIGNED:
            raise UnsupportedJoinError("BIN join offered without an assigned BIN")
        building_bin = resolution.building.bin
        scope = JoinScope.BUILDING

    return LotQueryPlan(bbl, boroid, block, lot, scope, building_bin)


def require_bin_join(resolution: ResolutionResult) -> str:
    """For sources keyed only by BIN. Refuses anything below building level."""
    if "bin" not in resolution.permitted_join_keys or resolution.building is None:
        level = resolution.resolution_level.value if resolution.resolution_level else "none"
        raise UnsupportedJoinError(
            f"BIN joins need a building-level resolution (got {resolution.outcome.value}, "
            f"level {level})"
        )
    return plan_lot_query(resolution).building_bin  # type: ignore[return-value]
