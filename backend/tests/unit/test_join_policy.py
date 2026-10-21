"""Phase 1's permitted_join_keys, enforced before any HPD request (synthetic resolutions)."""

import pytest

from nyc_apartment_agent.records.join_policy import (
    UnsupportedJoinError,
    compose_bbl,
    plan_lot_query,
    require_bin_join,
)
from nyc_apartment_agent.records.models import JoinScope
from nyc_apartment_agent.resolver.models import Outcome, ResolutionLevel
from tests.hpd_helpers import resolution


def test_building_level_plan_carries_bin():
    plan = plan_lot_query(resolution())
    assert plan.scope is JoinScope.BUILDING
    assert plan.building_bin == "2006185"
    assert (plan.boroid, plan.block, plan.lot) == (2, 2754, 5)


def test_tax_lot_plan_never_carries_a_bin_even_if_one_is_known():
    plan = plan_lot_query(resolution(bin_="2006185", level=ResolutionLevel.TAX_LOT))
    assert plan.scope is JoinScope.TAX_LOT
    assert plan.building_bin is None


@pytest.mark.parametrize(
    "outcome", [Outcome.AMBIGUOUS, Outcome.NO_RELIABLE_MATCH, Outcome.SOURCE_UNAVAILABLE]
)
def test_unverified_outcomes_are_refused(outcome):
    with pytest.raises(UnsupportedJoinError):
        plan_lot_query(resolution(outcome=outcome))


def test_bin_only_sources_refuse_tax_lot_results():
    with pytest.raises(UnsupportedJoinError):
        require_bin_join(resolution(level=ResolutionLevel.TAX_LOT))
    assert require_bin_join(resolution()) == "2006185"


def test_placeholder_bin_never_becomes_a_building_join():
    # Phase 1 never produces this combination; the policy refuses it anyway.
    r = resolution(bin_="2000000", level=ResolutionLevel.BUILDING)
    with pytest.raises(UnsupportedJoinError):
        plan_lot_query(r)


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("2", "2754", "5"), "2027540005"),
        ((1, 766, 55), "1007660055"),
        (("0", "1", "1"), None),
        (("2", "0", "5"), None),
        (("2", "2754", None), None),
        (("x", "2754", "5"), None),
    ],
)
def test_compose_bbl(parts, expected):
    assert compose_bbl(*parts) == expected
