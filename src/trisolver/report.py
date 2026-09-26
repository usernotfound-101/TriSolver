"""Terminal and markdown rendering of the triage board and the verdicts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .models import Action, Category, Decision, Routed, Verification

_COLOR = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m",
}

_CATEGORY_COLOR = {
    Category.REGRESSION: "red",
    Category.FLAKY: "yellow",
    Category.INFRA: "blue",
    Category.DEPENDENCY: "magenta",
    Category.CONFIG: "cyan",
}

_ACTION_LABEL = {
    Action.DISPATCH_TO_BOB: "-> bob",
    Action.RERUN_AND_QUARANTINE: "rerun+quarantine",
    Action.PING_INFRA: "ping infra",
    Action.PING_DEPS: "ping deps",
    Action.HUMAN_REVIEW: "human",
}

_DECISION_COLOR = {
    Decision.SURFACE: "green",
    Decision.BLOCK: "red",
    Decision.HUMAN_REVIEW: "yellow",
}


def _use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, *names: str) -> str:
    if not _use_color():
        return text
    return "".join(_COLOR[n] for n in names) + text + _COLOR["reset"]


def banner(jev_mode: str) -> str:
    if jev_mode == "simulated":
        return c(
            "  !! Jev is SIMULATED (no JEV_API_KEY). Answers are keyword heuristics,\n"
            "     not model output. Set JEV_API_KEY in .env for real verdicts.",
            "yellow",
        )
    return c(f"  jev: {jev_mode}", "dim")


def render_board(routed: list[Routed], jev_mode: str) -> str:
    lines = [
        "",
        c("  TRIAGE BOARD", "bold"),
        banner(jev_mode),
        "",
        c(f"  {'CATEGORY':<12} {'CONF':>5} {'PR':>5} {'SEVERITY':<16} {'ACTION':<17} UNIT", "dim"),
        c("  " + "-" * 96, "dim"),
    ]
    for r in routed:
        t = r.triage
        lines.append(
            "  "
            + c(f"{t.category.value:<12}", _CATEGORY_COLOR.get(t.category, "reset"))
            + f" {t.category_confidence:>4.0%}"
            + f" {t.caused_by_pr:>4.0%}"
            + f"  {t.severity_label:<15}"
            + c(f" {_ACTION_LABEL[r.action]:<17}", "bold" if r.action is Action.DISPATCH_TO_BOB else "dim")
            + _truncate(r.unit.name, 40)
            + (c(f"  x{r.unit.job_count} jobs", "dim") if r.unit.job_count > 1 else "")
        )
        lines.append(c(f"      {_truncate(r.unit.signature, 88)}", "dim"))
        lines.append(c(f"      why: {r.reason}", "dim"))
    counts: dict[str, int] = {}
    for r in routed:
        counts[_ACTION_LABEL[r.action]] = counts.get(_ACTION_LABEL[r.action], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    lines += ["", c(f"  {len(routed)} failure unit(s): {summary}", "bold"), ""]
    return "\n".join(lines)


def render_verification(routed: Routed, verification: Verification) -> str:
    colour = _DECISION_COLOR[verification.decision]
    head = c(f"  {verification.decision.value.upper():<14}", colour, "bold") + routed.unit.name
    lines = ["", head, ""]
    for key, value in verification.gates.items():
        bad = (
            (key == "weakens_tests" and value > 0.30)
            or (key == "touches_unrelated_files" and value > 0.50)
            or (key == "addresses_failure" and value < 0.60)
            or (key == "follows_project_docs" and value < 0.40)
        )
        bar = _bar(value)
        lines.append(
            f"    {key:<26} {value:>5.2f}  " + c(bar, "red" if bad else "green")
        )
    lines.append(c(f"    {'static check':<26}        {verification.static.summary()}", "dim"))
    lines.append(c(f"    {'files touched':<26}        {', '.join(verification.static.touched_files) or 'none'}", "dim"))
    for reason in verification.reasons:
        lines.append("    " + c(reason, colour))
    return "\n".join(lines)


def render_markdown(
    run_name: str,
    routed: list[Routed],
    verifications: dict[str, Verification],
    jev_mode: str,
) -> str:
    out = [
        f"# TriSolver report: `{run_name}`",
        "",
        f"Jev mode: **{jev_mode}**"
        + ("  \n> Simulated answers are keyword heuristics, not model output." if jev_mode == "simulated" else ""),
        "",
        "## Triage",
        "",
        "| category | confidence | from PR | severity | action | unit |",
        "| --- | ---: | ---: | --- | --- | --- |",
    ]
    for r in routed:
        t = r.triage
        out.append(
            f"| {t.category.value} | {t.category_confidence:.0%} | {t.caused_by_pr:.0%} | "
            f"{t.severity_label} | {_ACTION_LABEL[r.action]} | `{r.unit.name}` |"
        )

    if verifications:
        out += ["", "## Patch verification", ""]
        for r in routed:
            v = verifications.get(r.unit.id)
            if v is None:
                continue
            out += [
                f"### {v.decision.value.upper()} -- `{r.unit.name}`",
                "",
                "| gate | value |",
                "| --- | ---: |",
            ]
            out += [f"| {k} | {val:.2f} |" for k, val in v.gates.items()]
            out += [
                "",
                f"- static check: {v.static.summary()}",
                f"- files touched: {', '.join(v.static.touched_files) or 'none'}",
            ]
            out += [f"- {reason}" for reason in v.reasons]
            out.append("")
    return "\n".join(out) + "\n"


def write_report(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _bar(value: float, width: int = 20) -> str:
    filled = max(0, min(width, round(value * width)))
    return "#" * filled + "." * (width - filled)


def _truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
