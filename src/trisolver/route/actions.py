"""What each routing action means in practice.

These are the side effects a real deployment would perform. They are written
as explicit, inspectable plans rather than fired off directly, so a dry run
shows exactly what would happen and the demo never mutates a live repo by
accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Action, Routed


@dataclass(slots=True)
class Effect:
    """One concrete thing TriSolver would do about a routed failure."""

    kind: str  # "rerun" | "label" | "notify" | "assign" | "dispatch"
    target: str
    detail: str

    def render(self) -> str:
        return f"{self.kind}: {self.target} -- {self.detail}"


def plan_effects(routed: Routed) -> list[Effect]:
    unit, triage = routed.unit, routed.triage
    match routed.action:
        case Action.RERUN_AND_QUARANTINE:
            return [
                Effect("rerun", f"job:{unit.job}", "re-run failed jobs once to confirm flakiness"),
                Effect("label", "flaky-test", f"quarantine candidate: {unit.name}"),
                Effect("notify", "#ci-flakes", f"{unit.name} flagged flaky ({triage.category_confidence:.0%})"),
            ]
        case Action.PING_INFRA:
            return [
                Effect("notify", "#ci-infra", f"{unit.job} / {unit.step}: {unit.signature}"),
                Effect("label", "ci-infra", "no code change required"),
            ]
        case Action.PING_DEPS:
            return [
                Effect("notify", "#dependencies", f"{unit.step}: {unit.signature}"),
                Effect("label", "dependencies", "lockfile or version constraint needs a human decision"),
            ]
        case Action.DISPATCH_TO_BOB:
            return [
                Effect("dispatch", f"bob:{unit.id}", f"agent-mode subagent for {unit.name}"),
                Effect("label", "bob-proposed-fix", f"severity {triage.severity_label}"),
            ]
        case Action.HUMAN_REVIEW:
            return [
                Effect("assign", "pr-author", routed.reason),
                Effect("label", "needs-triage", "TriSolver was not confident enough to act"),
            ]
    return []
