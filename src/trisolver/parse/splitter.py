"""Split a raw Actions log into one FailureUnit per failing test or step.

Strategy, in order:

1. Framework-aware test splitting (pytest, jest/vitest, go test). A failing
   test is the most useful unit because it names exactly one behaviour.
2. Step-level splitting on ``##[error]`` for jobs that died before any test
   ran -- dependency resolution, a bad YAML key, a 429 from a registry.

A job that produced test failures is not also reported at step level: the
step error there is just "pytest exited 1", which tells a triager nothing the
test unit does not already say.
"""

from __future__ import annotations

import hashlib
import re

from ..models import FailureUnit
from . import patterns as P

_GENERIC_EXIT = re.compile(r"Process completed with exit code|The process .* failed with exit code")

_CONTEXT_BEFORE = 12
_CONTEXT_AFTER = 40


class _Job:
    __slots__ = ("name", "lines", "groups")

    def __init__(self, name: str) -> None:
        self.name = name
        self.lines: list[str] = []
        # (start_index, group_name) for every ##[group] seen, in order.
        self.groups: list[tuple[int, str]] = []

    def step_at(self, index: int) -> str:
        step = "unknown step"
        for start, name in self.groups:
            if start <= index:
                step = name
            else:
                break
        return step


def split_failures(raw_log: str, default_job: str = "build") -> list[FailureUnit]:
    """Parse a raw log into failure units. Never raises on malformed input."""
    units: list[FailureUnit] = []
    for job in _split_jobs(raw_log, default_job):
        found = (
            _split_pytest(job)
            or _split_jest(job)
            or _split_go(job)
        )
        if not found:
            found = _split_steps(job)
        units.extend(found)
    return _dedupe(units)


# --------------------------------------------------------------------------
# job segmentation


def _split_jobs(raw_log: str, default_job: str) -> list[_Job]:
    jobs: list[_Job] = []
    current = _Job(default_job)
    for raw_line in raw_log.splitlines():
        line = P.strip_timestamp(raw_line).rstrip("\r")
        header = P.JOB_HEADER.match(line)
        if header:
            if current.lines:
                jobs.append(current)
            current = _Job(header.group("job"))
            continue
        group = P.GROUP_START.match(line)
        if group:
            current.groups.append((len(current.lines), group.group("name").strip()))
        current.lines.append(line)
    if current.lines:
        jobs.append(current)
    return jobs


# --------------------------------------------------------------------------
# framework-aware splitting


def _split_pytest(job: _Job) -> list[FailureUnit]:
    """Split the FAILURES block into one unit per test case."""
    start = _find(job.lines, P.PYTEST_FAILURES_HEADER)
    if start is None:
        return []

    end = _find(job.lines, P.PYTEST_SUMMARY_HEADER, start + 1) or len(job.lines)
    reasons = _pytest_summary_reasons(job.lines, end)

    # Index every case header inside the FAILURES block, then slice between them.
    headers: list[tuple[int, str]] = []
    for i in range(start + 1, end):
        match = P.PYTEST_CASE_HEADER.match(job.lines[i])
        if match:
            headers.append((i, match.group("name").strip()))
    if not headers:
        return []

    units: list[FailureUnit] = []
    for pos, (line_no, name) in enumerate(headers):
        stop = headers[pos + 1][0] if pos + 1 < len(headers) else end
        excerpt = "\n".join(job.lines[line_no:stop]).strip()
        reason = _match_reason(name, reasons)
        if reason:
            excerpt = f"{excerpt}\n\nsummary: {reason}"
        units.append(
            _unit(
                kind="test",
                name=name,
                job=job.name,
                step=job.step_at(line_no),
                excerpt=excerpt,
                files=_implicated(
                    excerpt,
                    (P.PYTEST_TRACEBACK_FILE, P.PYTEST_TRACEBACK_FRAME, P.ANY_SOURCE_FILE),
                ),
            )
        )
    return units


def _pytest_summary_reasons(lines: list[str], summary_start: int) -> dict[str, str]:
    """Map ``tests/x.py::test_y`` -> the one-line reason pytest printed."""
    reasons: dict[str, str] = {}
    for line in lines[summary_start:]:
        match = P.PYTEST_SUMMARY_LINE.match(line.strip())
        if match:
            reasons[match.group("name")] = (match.group("reason") or "").strip()
    return reasons


def _match_reason(case_name: str, reasons: dict[str, str]) -> str:
    """Case headers print ``test_y`` or ``Class.test_y``; summaries print the
    full node id. Match on the trailing segment."""
    if case_name in reasons:
        return reasons[case_name]
    tail = case_name.split("::")[-1].split(".")[-1]
    for node_id, reason in reasons.items():
        if node_id.split("::")[-1].split(".")[-1] == tail:
            return reason
    return ""


def _split_jest(job: _Job) -> list[FailureUnit]:
    headers = [
        (i, match.group("name").strip())
        for i, line in enumerate(job.lines)
        if (match := P.JEST_CASE_HEADER.match(line))
    ]
    if not headers:
        return []
    units: list[FailureUnit] = []
    for pos, (line_no, name) in enumerate(headers):
        stop = headers[pos + 1][0] if pos + 1 < len(headers) else min(line_no + 60, len(job.lines))
        excerpt = "\n".join(job.lines[line_no:stop]).strip()
        units.append(
            _unit(
                kind="test",
                name=name,
                job=job.name,
                step=job.step_at(line_no),
                excerpt=excerpt,
                files=_implicated(excerpt, (P.JS_STACK_FILE,)),
            )
        )
    return units


def _split_go(job: _Job) -> list[FailureUnit]:
    headers = [
        (i, match.group("name").strip())
        for i, line in enumerate(job.lines)
        if (match := P.GO_CASE_HEADER.match(line))
    ]
    if not headers:
        return []
    units: list[FailureUnit] = []
    for pos, (line_no, name) in enumerate(headers):
        stop = headers[pos + 1][0] if pos + 1 < len(headers) else min(line_no + 60, len(job.lines))
        excerpt = "\n".join(job.lines[line_no:stop]).strip()
        units.append(
            _unit(
                kind="test",
                name=name,
                job=job.name,
                step=job.step_at(line_no),
                excerpt=excerpt,
                files=_implicated(excerpt, (P.GO_FILE,)),
            )
        )
    return units


# --------------------------------------------------------------------------
# step-level fallback


def _split_steps(job: _Job) -> list[FailureUnit]:
    """One unit per ``##[error]``, carrying the surrounding log as context."""
    units: list[FailureUnit] = []
    seen_steps: set[str] = set()
    for i, line in enumerate(job.lines):
        match = P.ERROR_LINE.match(line)
        if not match:
            continue
        step = job.step_at(i)
        # Several ##[error] lines usually belong to one broken step; the first
        # carries the real message and the rest are the runner echoing exit codes.
        if step in seen_steps:
            continue
        seen_steps.add(step)
        lo = max(0, i - _CONTEXT_BEFORE)
        hi = min(len(job.lines), i + _CONTEXT_AFTER)
        excerpt = "\n".join(job.lines[lo:hi]).strip()
        units.append(
            _unit(
                kind="step",
                name=step,
                job=job.name,
                step=step,
                excerpt=excerpt,
                files=_implicated(excerpt, (P.ANY_SOURCE_FILE,)),
            )
        )
    return units


# --------------------------------------------------------------------------
# helpers


def _unit(*, kind: str, name: str, job: str, step: str, excerpt: str, files: list[str]) -> FailureUnit:
    signature = _signature(excerpt)
    digest = hashlib.sha1(f"{job}/{name}".encode(), usedforsecurity=False).hexdigest()[:8]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()[:48] or "failure"
    return FailureUnit(
        id=f"{slug}-{digest}",
        kind=kind,
        name=name,
        job=job,
        step=step,
        log_excerpt=excerpt,
        signature=signature,
        implicated_files=files,
    )


def _signature(excerpt: str) -> str:
    """The most informative error line in the excerpt, for board display.

    Scored rather than first-match: a step that failed on a 429 ends with
    "Process completed with exit code 18", and that line is true, generic,
    and useless. The rate-limit line above it is the one a human reads.
    """
    best_line, best_score = "", 0.0
    for raw in excerpt.splitlines():
        line = raw.strip()
        if not line:
            continue
        score = 0.0
        if line.startswith("E "):
            score += 3.0
        if P.ERROR_LINE.match(line):
            score += 2.5
        if re.search(r"\b(?:error|failed|failure|exception)\b", line, re.IGNORECASE):
            score += 1.5
        if re.search(r"\b(?:timeout|timed out|refused|conflict|\d{3} \w)", line, re.IGNORECASE):
            score += 1.0
        if "assert" in line.lower():
            score += 1.0
        if _GENERIC_EXIT.search(line):
            score -= 4.0
        if line.startswith("##[group]") or line.startswith("+ "):
            score -= 2.0
        if score > best_score:
            best_line, best_score = line, score

    if not best_line:
        first = next((l.strip() for l in excerpt.splitlines() if l.strip()), "")
        best_line = first
    best_line = P.ERROR_LINE.sub(r"\g<message>", best_line).removeprefix("E ").strip()
    return best_line[:200]


def _implicated(excerpt: str, regexes: tuple[re.Pattern[str], ...]) -> list[str]:
    """Source files named in the excerpt, de-duplicated, runtime noise dropped."""
    found: list[str] = []
    for line in excerpt.splitlines():
        for regex in regexes:
            for match in regex.finditer(line):
                path = match.group("path")
                if P.is_noise_path(path) or path in found:
                    continue
                found.append(path)
    return found[:12]


def _find(lines: list[str], regex: re.Pattern[str], start: int = 0) -> int | None:
    for i in range(start, len(lines)):
        if regex.match(lines[i].strip()):
            return i
    return None


def _dedupe(units: list[FailureUnit]) -> list[FailureUnit]:
    seen: set[str] = set()
    out: list[FailureUnit] = []
    for unit in units:
        if unit.id in seen:
            continue
        seen.add(unit.id)
        out.append(unit)
    return out
