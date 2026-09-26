"""Stage 3: ask Jev one typed question set per failure unit."""

from __future__ import annotations

from .jev import JevClient, SEVERITY_LEGEND, TRIAGE_QUESTIONS
from .jev import schema
from .models import Category, DiffSummary, FailureUnit, Triage

_STATE_TEMPLATE = """\
## Failure unit
id: {unit_id}
kind: {kind}
job: {job}
step: {step}
name: {name}

## Log excerpt
{log}

## Files named in the failure
{implicated}

## Pull request diff summary
{diff}
"""


def build_state(unit: FailureUnit, diff: DiffSummary) -> str:
    """The single 'state' blob Jev evaluates. Kept well inside the 64k window."""
    return _STATE_TEMPLATE.format(
        unit_id=unit.id,
        kind=unit.kind,
        job=unit.job,
        step=unit.step,
        name=unit.name,
        log=unit.as_text(),
        implicated="\n".join(f"- {p}" for p in unit.implicated_files) or "- (none identified)",
        diff=diff.as_text(),
    )


def diff_overlap(unit: FailureUnit, diff: DiffSummary) -> float:
    """How much of what the failure blamed, this PR actually changed.

    Measured over the *source* files the failure named, not the test files.
    A failing test always names its own file, so including it would dilute
    the signal to noise on every unit equally.

    Used two ways: as evidence in the state text, and as the single hint the
    offline simulator leans on so its answers are not pure keyword bingo.
    """
    from .verify.static import is_test_path

    candidates = [p for p in unit.implicated_files if not is_test_path(p)]
    if not candidates:
        candidates = unit.implicated_files
    if not candidates:
        return 0.0

    changed = set(diff.files)
    hits = sum(1 for path in candidates if _matches_any(path, changed))
    return hits / len(candidates)


def _matches_any(path: str, changed: set[str]) -> bool:
    """A traceback prints ``pricing.py``; a diff prints ``src/app/pricing.py``."""
    return any(
        path == c or c.endswith("/" + path) or path.endswith("/" + c) or c == path
        for c in changed
    )


def triage_unit(client: JevClient, unit: FailureUnit, diff: DiffSummary) -> Triage:
    """One failure unit in, one typed triage out."""
    state = build_state(unit, diff)
    overlap = diff_overlap(unit, diff)

    # Category first: severity is scored relative to the category the model
    # settled on, so the simulator needs it as a hint. Live Jev answers all
    # three in a single round trip.
    response = client.ask(
        state,
        TRIAGE_QUESTIONS,
        hints={"diff_overlap": overlap, "unit_kind": unit.kind},
    )
    answers = response.answers

    category_name, probabilities, confidence = schema.choice(answers.get("category", {}))
    category = _coerce_category(category_name, probabilities)

    if not response.is_real:
        # Second simulator pass so severity can react to the chosen category.
        response = client.ask(
            state,
            TRIAGE_QUESTIONS,
            hints={"diff_overlap": overlap, "unit_kind": unit.kind, "category": category.value},
        )
        answers = response.answers

    caused_by_pr = schema.noul(answers.get("caused_by_pr", {}))
    severity, legend, _ = schema.score(answers.get("severity", {}), SEVERITY_LEGEND)

    return Triage(
        unit_id=unit.id,
        category=category,
        category_probabilities=probabilities,
        category_confidence=confidence,
        caused_by_pr=caused_by_pr,
        severity=severity,
        severity_legend=_short_legend(legend),
        jev_mode=response.mode,
        model=response.model,
    )


def _coerce_category(name: str, probabilities: dict[str, float]) -> Category:
    try:
        return Category(name)
    except ValueError:
        pass
    for candidate in sorted(probabilities, key=lambda k: probabilities[k], reverse=True):
        try:
            return Category(candidate)
        except ValueError:
            continue
    return Category.REGRESSION


def _short_legend(legend: list[str]) -> list[str]:
    """Legends come back as full rubric sentences; the board wants one word."""
    if len(legend) == len(SEVERITY_LEGEND) and all(
        item.lower().startswith(short.split()[0]) for item, short in zip(legend, SEVERITY_LEGEND)
    ):
        return list(SEVERITY_LEGEND)
    return [item.split(".")[0].strip()[:24] for item in legend] or list(SEVERITY_LEGEND)
