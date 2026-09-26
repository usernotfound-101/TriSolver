"""Stage 5a: write one task brief per high-confidence regression.

Each brief is a self-contained packet for a single Bob subagent: the failure,
the files it implicates, the PR diff summary, and the project's own
conventions. The acceptance criteria are stated up front and are word for
word the gates the patch will be judged against, because an agent told how
it will be checked is far likelier to produce something that passes honestly.
"""

from __future__ import annotations

from pathlib import Path

from ..ingest import RunBundle
from ..models import Routed

_TEMPLATE = """\
# Fix: {name}

- **unit id**: `{unit_id}`
- **job / step**: {job} / {step}
- **category**: {category} ({confidence:.0%} confidence)
- **attributed to this PR**: {caused_by_pr:.0%}
- **severity**: {severity_label} ({severity:.2f})
- **repo**: {repo}{pr_line}

## What failed

```
{signature}
```

## Failure log

```
{log}
```

## Files named in the failure

{implicated}

## What this pull request changed

```
{diff}
```

## Project conventions

{docs}

## Your task

Propose the smallest change that makes this failure pass for the right reason.

Write the result as a unified diff to:

    {patch_path}

## Acceptance criteria

This patch is checked automatically before any human sees it. It is rejected if:

1. It does not address the cause of the failure.
2. It touches files that this fix does not require.
3. It deletes, skips, renames out of collection, or otherwise weakens any test,
   including loosening an assertion or widening a tolerance.
4. It contradicts the project conventions quoted above.

Fix the code. Do not fix the test.
"""


def render_brief(routed: Routed, bundle: RunBundle, patch_path: Path) -> str:
    unit, triage = routed.unit, routed.triage
    pr_line = f"\n- **pull request**: #{bundle.pr_number}" if bundle.pr_number else ""
    implicated = "\n".join(f"- `{p}`" for p in unit.implicated_files) or "- _(none identified)_"
    docs = bundle.docs.strip() or "_No CONTRIBUTING or runbook found for this repository._"
    return _TEMPLATE.format(
        name=unit.name,
        unit_id=unit.id,
        job=unit.job,
        step=unit.step,
        category=triage.category.value,
        confidence=triage.category_confidence,
        caused_by_pr=triage.caused_by_pr,
        severity_label=triage.severity_label,
        severity=triage.severity,
        repo=bundle.repo,
        pr_line=pr_line,
        signature=unit.signature or "(no single-line signature extracted)",
        log=unit.as_text(4000),
        implicated=implicated,
        diff=bundle.diff.as_text(),
        docs=docs,
        patch_path=patch_path,
    )


def write_brief(routed: Routed, bundle: RunBundle, briefs_dir: Path, patches_dir: Path) -> Path:
    briefs_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patches_dir / f"{routed.unit.id}.patch"
    brief_path = briefs_dir / f"{routed.unit.id}.md"
    brief_path.write_text(render_brief(routed, bundle, patch_path), encoding="utf-8")
    return brief_path
