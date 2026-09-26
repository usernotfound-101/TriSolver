"""Regexes for turning raw Actions logs into failure units.

Nothing here calls a model. Splitting a log is a parsing problem and parsing
code does it deterministically, cheaply, and the same way every run.
"""

from __future__ import annotations

import re

# GitHub prefixes every raw log line with an ISO-8601 timestamp.
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s?")

# Actions workflow commands.
GROUP_START = re.compile(r"^##\[group\](?P<name>.*)$")
GROUP_END = re.compile(r"^##\[endgroup\]")
ERROR_LINE = re.compile(r"^##\[error\](?P<message>.*)$")

# Fixture-only convenience: one file can hold several jobs.
JOB_HEADER = re.compile(r"^===\s*JOB:\s*(?P<job>.+?)\s*===$")

# --- pytest ---------------------------------------------------------------
PYTEST_FAILURES_HEADER = re.compile(r"^=+\s*(FAILURES|ERRORS)\s*=+$")
PYTEST_SUMMARY_HEADER = re.compile(r"^=+\s*short test summary info\s*=+$")
PYTEST_CASE_HEADER = re.compile(r"^_{3,}\s+(?P<name>.+?)\s+_{3,}$")
PYTEST_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(?P<name>\S+)(?:\s+-\s+(?P<reason>.*))?$")
PYTEST_TRACEBACK_FILE = re.compile(r"^(?P<path>[\w./-]+\.py):(?P<line>\d+):")
PYTEST_TRACEBACK_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)')

# --- jest / vitest --------------------------------------------------------
JEST_CASE_HEADER = re.compile(r"^\s*●\s+(?P<name>(?!Console)\S.*?)\s*$")
JS_STACK_FILE = re.compile(r"\(?(?P<path>(?:src|lib|app|test|tests)/[\w./-]+\.[jt]sx?):(?P<line>\d+)")

# --- go test --------------------------------------------------------------
GO_CASE_HEADER = re.compile(r"^\s*--- FAIL:\s+(?P<name>\S+)")
GO_FILE = re.compile(r"^\s*(?P<path>[\w./-]+\.go):(?P<line>\d+):")

# --- generic --------------------------------------------------------------
ANY_SOURCE_FILE = re.compile(
    r"(?P<path>(?:[\w.-]+/)*[\w.-]+\.(?:py|[jt]sx?|go|rs|java|rb|kt|cs|php))(?::(?P<line>\d+))?"
)

# Paths that are never the code under test, so never worth implicating.
NOISE_PATH_PARTS = (
    "site-packages/",
    "node_modules/",
    "/usr/lib/",
    "/opt/hostedtoolcache/",
    "dist-packages/",
    ".venv/",
)


def strip_timestamp(line: str) -> str:
    return TIMESTAMP.sub("", line)


def is_noise_path(path: str) -> bool:
    return any(part in path for part in NOISE_PATH_PARTS)
