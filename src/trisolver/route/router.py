"""Stage 4: code routes on Jev's answer and its confidence.

Jev decides *what the failure is*. This module decides *what to do about it*,
and it is deliberately boring, deterministic, readable code -- the kind you
can put in front of a release manager and argue about line by line.

The one rule worth stating out loud: confidence gates automation. A category
TriSolver is unsure about never triggers a rerun, a ping, or an agent. It
goes to a person, which is exactly where it would have gone anyway.
"""

from __future__ import annotations

from ..config import (
    MIN_CATEGORY_CONFIDENCE,
    MIN_CAUSED_BY_PR,
    MIN_DISPATCH_CONFIDENCE,
)
from ..models import Action, Category, FailureUnit, Routed, Triage


def route_unit(unit: FailureUnit, triage: Triage) -> Routed:
    confidence = triage.category_confidence

    if confidence < MIN_CATEGORY_CONFIDENCE:
        return Routed(
            unit=unit,
            triage=triage,
            action=Action.HUMAN_REVIEW,
            reason=(
                f"category confidence {confidence:.0%} is below the "
                f"{MIN_CATEGORY_CONFIDENCE:.0%} floor for automated handling"
            ),
            labels=["needs-triage"],
        )

    match triage.category:
        case Category.FLAKY:
            return Routed(
                unit, triage, Action.RERUN_AND_QUARANTINE,
                f"non-deterministic failure ({confidence:.0%}); rerun once and quarantine if it passes",
                ["flaky-test"],
            )
        case Category.INFRA:
            return Routed(
                unit, triage, Action.PING_INFRA,
                f"CI environment failure ({confidence:.0%}); no code change will fix it",
                ["ci-infra"],
            )
        case Category.DEPENDENCY:
            return Routed(
                unit, triage, Action.PING_DEPS,
                f"dependency drift ({confidence:.0%}); pinning is a human decision",
                ["dependencies"],
            )
        case Category.CONFIG:
            return Routed(
                unit, triage, Action.HUMAN_REVIEW,
                f"configuration error ({confidence:.0%}); the fix lives outside the code under test",
                ["ci-config"],
            )
        case Category.REGRESSION:
            return _route_regression(unit, triage, confidence)

    return Routed(unit, triage, Action.HUMAN_REVIEW, "unrecognised category", ["needs-triage"])


def _route_regression(unit: FailureUnit, triage: Triage, confidence: float) -> Routed:
    if triage.caused_by_pr < MIN_CAUSED_BY_PR:
        return Routed(
            unit, triage, Action.HUMAN_REVIEW,
            (
                f"real regression, but only {triage.caused_by_pr:.0%} likely to come from this PR; "
                "probably pre-existing on the base branch"
            ),
            ["needs-triage", "possibly-pre-existing"],
        )
    if confidence < MIN_DISPATCH_CONFIDENCE:
        return Routed(
            unit, triage, Action.HUMAN_REVIEW,
            (
                f"looks like a regression but {confidence:.0%} confidence is below the "
                f"{MIN_DISPATCH_CONFIDENCE:.0%} bar to spend an agent on it"
            ),
            ["needs-triage"],
        )
    return Routed(
        unit, triage, Action.DISPATCH_TO_BOB,
        (
            f"high-confidence regression ({confidence:.0%}) attributed to this PR "
            f"({triage.caused_by_pr:.0%}), severity {triage.severity_label}"
        ),
        ["bob-proposed-fix"],
    )


def route_all(pairs: list[tuple[FailureUnit, Triage]]) -> list[Routed]:
    """Route every unit, most severe regression first."""
    routed = [route_unit(unit, triage) for unit, triage in pairs]
    routed.sort(key=lambda r: (r.action is not Action.DISPATCH_TO_BOB, -r.triage.severity))
    return routed
