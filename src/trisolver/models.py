"""Core data types passed between the pipeline stages.

The pipeline is a straight line:

    ingest -> parse -> triage (Jev) -> route -> dispatch (Bob) -> verify (Jev)

Every stage consumes and produces the dataclasses below, so each one can be
run standalone from the CLI and the intermediate state can be dumped to JSON.
"""

from __future__ import annotations

import dataclasses
import enum
from dataclasses import dataclass, field
from typing import Any


class Category(enum.StrEnum):
    """What kind of failure this is. Mirrors the Jev Choice criteria keys."""

    REGRESSION = "regression"
    FLAKY = "flaky"
    INFRA = "infra"
    DEPENDENCY = "dependency"
    CONFIG = "config"


class Action(enum.StrEnum):
    """What code does with a triaged unit. Chosen by the router, not by Jev."""

    DISPATCH_TO_BOB = "dispatch_to_bob"
    RERUN_AND_QUARANTINE = "rerun_and_quarantine"
    PING_INFRA = "ping_infra"
    PING_DEPS = "ping_deps"
    HUMAN_REVIEW = "human_review"


class Decision(enum.StrEnum):
    """Verdict on a patch Bob proposed."""

    SURFACE = "surface"
    BLOCK = "block"
    HUMAN_REVIEW = "human_review"


@dataclass(slots=True)
class DiffSummary:
    """A compact description of the PR diff, small enough to sit in a prompt."""

    files: list[str] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    raw: str = ""

    def as_text(self, max_files: int = 40) -> str:
        shown = self.files[:max_files]
        more = len(self.files) - len(shown)
        lines = [f"{len(self.files)} file(s) changed, +{self.added_lines}/-{self.removed_lines}"]
        lines += [f"  {p}" for p in shown]
        if more > 0:
            lines.append(f"  ... and {more} more")
        return "\n".join(lines)


@dataclass(slots=True)
class FailureUnit:
    """One failing test or one failing step. The atom of triage."""

    id: str
    kind: str  # "test" | "step"
    name: str
    job: str
    step: str
    log_excerpt: str
    signature: str = ""
    implicated_files: list[str] = field(default_factory=list)

    def as_text(self, max_chars: int = 6000) -> str:
        body = self.log_excerpt
        if len(body) > max_chars:
            head = body[: max_chars // 2]
            tail = body[-max_chars // 2 :]
            body = f"{head}\n...[{len(self.log_excerpt) - max_chars} chars elided]...\n{tail}"
        return body


@dataclass(slots=True)
class Triage:
    """Jev's typed answers for one failure unit, plus how they were obtained."""

    unit_id: str
    category: Category
    category_probabilities: dict[str, float]
    category_confidence: float
    caused_by_pr: float  # noul: 0..1
    severity: float  # score: 0..len(legend)-1
    severity_legend: list[str]
    jev_mode: str  # "live" | "simulated"
    model: str = ""

    @property
    def severity_label(self) -> str:
        if not self.severity_legend:
            return f"{self.severity:.2f}"
        idx = max(0, min(len(self.severity_legend) - 1, round(self.severity)))
        return self.severity_legend[idx]


@dataclass(slots=True)
class Routed:
    """The router's decision for one triaged unit."""

    unit: FailureUnit
    triage: Triage
    action: Action
    reason: str
    labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PatchProposal:
    """A unified diff Bob proposes for one regression."""

    unit_id: str
    patch_text: str
    source: str  # "inbox" | "bob-cli" | "fixture"
    notes: str = ""


@dataclass(slots=True)
class StaticFlags:
    """Deterministic findings from reading the patch. No model involved."""

    removed_test_functions: list[str] = field(default_factory=list)
    added_skip_markers: list[str] = field(default_factory=list)
    removed_assertions: int = 0
    deleted_test_files: list[str] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)

    @property
    def weakens_tests(self) -> bool:
        return bool(
            self.removed_test_functions
            or self.added_skip_markers
            or self.deleted_test_files
            or self.removed_assertions > 0
        )

    def summary(self) -> str:
        bits: list[str] = []
        if self.deleted_test_files:
            bits.append(f"deleted test file(s): {', '.join(self.deleted_test_files)}")
        if self.removed_test_functions:
            bits.append(f"removed test(s): {', '.join(self.removed_test_functions)}")
        if self.added_skip_markers:
            bits.append(f"added skip/xfail: {', '.join(self.added_skip_markers)}")
        if self.removed_assertions:
            bits.append(f"removed {self.removed_assertions} assertion(s)")
        return "; ".join(bits) or "no test weakening detected"


@dataclass(slots=True)
class Verification:
    """Jev's gate answers on a patch, merged with the static findings."""

    unit_id: str
    gates: dict[str, float]
    static: StaticFlags
    decision: Decision
    reasons: list[str]
    jev_mode: str


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/enums into JSON-safe structures."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
