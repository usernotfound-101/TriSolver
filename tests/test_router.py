"""Routing is deterministic policy: pin every branch and every threshold."""

import pytest

from trisolver.config import MIN_CATEGORY_CONFIDENCE, MIN_CAUSED_BY_PR, MIN_DISPATCH_CONFIDENCE
from trisolver.models import Action, Category, FailureUnit, Triage
from trisolver.route import route_all, route_unit


def make_unit(name: str = "test_x") -> FailureUnit:
    return FailureUnit(id=name, kind="test", name=name, job="ci", step="pytest", log_excerpt="boom")


def make_triage(category: Category, confidence: float, caused_by_pr: float = 0.9, severity: float = 2.0) -> Triage:
    return Triage(
        unit_id="test_x",
        category=category,
        category_probabilities={category.value: confidence},
        category_confidence=confidence,
        caused_by_pr=caused_by_pr,
        severity=severity,
        severity_legend=["cosmetic", "minor", "major", "release blocker"],
        jev_mode="simulated",
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (Category.FLAKY, Action.RERUN_AND_QUARANTINE),
        (Category.INFRA, Action.PING_INFRA),
        (Category.DEPENDENCY, Action.PING_DEPS),
        (Category.CONFIG, Action.HUMAN_REVIEW),
        (Category.REGRESSION, Action.DISPATCH_TO_BOB),
    ],
)
def test_confident_categories_route_to_their_owner(category, expected):
    routed = route_unit(make_unit(), make_triage(category, 0.92))
    assert routed.action is expected


def test_low_confidence_never_automates():
    """Below the floor, every category goes to a person -- including flaky."""
    for category in Category:
        routed = route_unit(make_unit(), make_triage(category, MIN_CATEGORY_CONFIDENCE - 0.01))
        assert routed.action is Action.HUMAN_REVIEW
        assert "below the" in routed.reason


def test_regression_not_attributed_to_the_pr_goes_to_a_human():
    routed = route_unit(
        make_unit(), make_triage(Category.REGRESSION, 0.95, caused_by_pr=MIN_CAUSED_BY_PR - 0.01)
    )
    assert routed.action is Action.HUMAN_REVIEW
    assert "pre-existing" in routed.reason
    assert "possibly-pre-existing" in routed.labels


def test_regression_below_the_dispatch_bar_is_not_worth_an_agent():
    confidence = (MIN_CATEGORY_CONFIDENCE + MIN_DISPATCH_CONFIDENCE) / 2
    routed = route_unit(make_unit(), make_triage(Category.REGRESSION, confidence))
    assert routed.action is Action.HUMAN_REVIEW
    assert "spend an agent" in routed.reason


def test_dispatch_needs_both_confidence_and_attribution():
    routed = route_unit(
        make_unit(),
        make_triage(Category.REGRESSION, MIN_DISPATCH_CONFIDENCE, caused_by_pr=MIN_CAUSED_BY_PR),
    )
    assert routed.action is Action.DISPATCH_TO_BOB


def test_regressions_sort_first_then_by_severity():
    pairs = [
        (make_unit("flaky_one"), make_triage(Category.FLAKY, 0.9, severity=3.0)),
        (make_unit("low_sev"), make_triage(Category.REGRESSION, 0.9, severity=1.0)),
        (make_unit("high_sev"), make_triage(Category.REGRESSION, 0.9, severity=3.0)),
    ]
    order = [r.unit.name for r in route_all(pairs)]
    assert order == ["high_sev", "low_sev", "flaky_one"]
