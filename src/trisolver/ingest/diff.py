"""Turn a unified diff into a summary small enough to put in a prompt."""

from __future__ import annotations

import re

from ..models import DiffSummary

_FILE_HEADER = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$")
_PLUS_PLUS_PLUS = re.compile(r"^\+\+\+ b/(?P<path>.+)$")


def summarize_diff(diff_text: str) -> DiffSummary:
    files: list[str] = []
    added = removed = 0
    for line in diff_text.splitlines():
        header = _FILE_HEADER.match(line)
        if header:
            path = header.group("b")
            if path not in files:
                files.append(path)
            continue
        target = _PLUS_PLUS_PLUS.match(line)
        if target:
            path = target.group("path")
            if path != "/dev/null" and path not in files:
                files.append(path)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return DiffSummary(files=files, added_lines=added, removed_lines=removed, raw=diff_text)
