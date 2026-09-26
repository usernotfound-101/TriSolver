"""Stage 1: get a failed run's logs and its PR diff, from disk or from GitHub."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..models import DiffSummary
from .diff import summarize_diff

FIXTURES_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "runs"


@dataclass(slots=True)
class RunBundle:
    """Everything the pipeline needs about one red CI run."""

    name: str
    repo: str
    raw_log: str
    diff: DiffSummary
    pr_number: int | None = None
    run_id: int | None = None
    workflow: str = ""
    title: str = ""
    docs: str = ""  # CONTRIBUTING / runbook excerpt handed to Bob and to the gates
    source: str = "fixture"
    extra: dict = field(default_factory=dict)


def load_run(target: str, *, live: bool = False, token: str | None = None) -> RunBundle:
    """Load a run by fixture name, fixture path, or GitHub Actions run URL."""
    if live or target.startswith("http"):
        from .github import fetch_run  # imported lazily: fixtures need no network

        return fetch_run(target, token=token)
    return load_fixture(target)


def load_fixture(name_or_path: str) -> RunBundle:
    path = Path(name_or_path)
    if not path.is_dir():
        path = FIXTURES_ROOT / name_or_path
    if not path.is_dir():
        available = ", ".join(sorted(p.name for p in FIXTURES_ROOT.iterdir() if p.is_dir())) or "none"
        raise FileNotFoundError(f"No fixture named {name_or_path!r}. Available: {available}")

    meta_path = path / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    diff_path = path / "diff.patch"
    docs_path = path / "CONTRIBUTING.md"

    return RunBundle(
        name=path.name,
        repo=meta.get("repo", "example/repo"),
        raw_log=(path / "logs.txt").read_text(encoding="utf-8"),
        diff=summarize_diff(diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""),
        pr_number=meta.get("pr_number"),
        run_id=meta.get("run_id"),
        workflow=meta.get("workflow", "ci.yml"),
        title=meta.get("title", ""),
        docs=docs_path.read_text(encoding="utf-8") if docs_path.is_file() else "",
        source="fixture",
        extra=meta,
    )


def list_fixtures() -> list[str]:
    if not FIXTURES_ROOT.is_dir():
        return []
    return sorted(p.name for p in FIXTURES_ROOT.iterdir() if p.is_dir())
