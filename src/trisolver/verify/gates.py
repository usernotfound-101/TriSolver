"""Stage 6: Jev verifies Bob's patch before a human ever sees it.

Two independent checks have to agree before a patch is surfaced:

* the deterministic reading of the diff in :mod:`.static`, and
* Jev's typed Noul gates.

Either one can block on its own. That asymmetry is the point -- a cheat that
the regex misses can still be caught by the model, and a model that is having
a bad day cannot wave through a deleted test.
"""

from __future__ import annotations

from ..config import MAX_UNRELATED_FILES, MAX_WEAKENS_TESTS, MIN_ADDRESSES_FAILURE
from ..ingest import RunBundle
from ..jev import JevClient, VERIFY_QUESTIONS
from ..jev import schema
from ..models import Decision, PatchProposal, Routed, Verification
from .static import analyze_patch, unrelated_ratio

_STATE_TEMPLATE = """\
## The CI failure this patch is supposed to fix

test/step: {name}
signature: {signature}

```
{log}
```

## Files the failure named

{implicated}

## The proposed patch

```diff
{patch}
```

## Project conventions

{docs}
"""

_BLOCK_UNRELATED = 0.80  # above this, unrelated churn is not a judgement call


def build_state(routed: Routed, proposal: PatchProposal, bundle: RunBundle) -> str:
    unit = routed.unit
    return _STATE_TEMPLATE.format(
        name=unit.name,
        signature=unit.signature,
        log=unit.as_text(3000),
        implicated="\n".join(f"- {p}" for p in unit.implicated_files) or "- (none identified)",
        patch=proposal.patch_text[:12000],
        docs=(bundle.docs.strip()[:4000] or "_none provided_"),
    )


def verify_patch(
    client: JevClient,
    routed: Routed,
    proposal: PatchProposal,
    bundle: RunBundle,
) -> Verification:
    static = analyze_patch(proposal.patch_text)

    expected = set(routed.unit.implicated_files) | set(bundle.diff.files)
    unrelated = unrelated_ratio(static, expected)
    touches_source = any(not _looks_like_test(p) for p in static.touched_files)

    response = client.ask(
        build_state(routed, proposal, bundle),
        VERIFY_QUESTIONS,
        hints={
            "static_weakens": static.weakens_tests,
            "unrelated_ratio": round(unrelated, 3),
            "addresses_failure": 0.88 if touches_source and not static.weakens_tests else 0.15,
        },
    )

    gates = {key: schema.noul(response.answers.get(key, {})) for key in VERIFY_QUESTIONS}
    decision, reasons = _decide(gates, static, unrelated, proposal)

    return Verification(
        unit_id=routed.unit.id,
        gates=gates,
        static=static,
        decision=decision,
        reasons=reasons,
        jev_mode=response.mode,
    )


def _decide(
    gates: dict[str, float],
    static,
    unrelated: float,
    proposal: PatchProposal,
) -> tuple[Decision, list[str]]:
    reasons: list[str] = []

    if not proposal.patch_text.strip():
        return Decision.HUMAN_REVIEW, ["Bob produced no patch for this regression"]

    # --- the anti-cheat gate, checked twice and blocking on either signal ---
    if static.weakens_tests:
        reasons.append(f"BLOCKED by static check: {static.summary()}")
    if gates.get("weakens_tests", 0.0) > MAX_WEAKENS_TESTS:
        reasons.append(
            f"BLOCKED by Jev: weakens_tests={gates['weakens_tests']:.2f} "
            f"(threshold {MAX_WEAKENS_TESTS:.2f})"
        )
    if reasons:
        reasons.append("The failing test was neutralised rather than the defect fixed.")
        return Decision.BLOCK, reasons

    # --- scope ---
    if gates.get("touches_unrelated_files", 0.0) > _BLOCK_UNRELATED or unrelated > _BLOCK_UNRELATED:
        return Decision.BLOCK, [
            f"BLOCKED: patch is mostly unrelated churn "
            f"(jev={gates.get('touches_unrelated_files', 0.0):.2f}, static={unrelated:.2f})"
        ]
    if gates.get("touches_unrelated_files", 0.0) > MAX_UNRELATED_FILES or unrelated > MAX_UNRELATED_FILES:
        reasons.append(
            f"patch touches files beyond the fix "
            f"(jev={gates.get('touches_unrelated_files', 0.0):.2f}, static={unrelated:.2f})"
        )

    # --- does it actually fix it ---
    addresses = gates.get("addresses_failure", 0.0)
    if addresses < 0.35:
        return Decision.BLOCK, [f"BLOCKED: does not address the failure (addresses={addresses:.2f})"]
    if addresses < MIN_ADDRESSES_FAILURE:
        reasons.append(
            f"only {addresses:.0%} confident the patch addresses the failure "
            f"(threshold {MIN_ADDRESSES_FAILURE:.0%})"
        )

    # --- house style ---
    if gates.get("follows_project_docs", 1.0) < 0.40:
        reasons.append(
            f"conflicts with the project's stated conventions "
            f"(follows_project_docs={gates['follows_project_docs']:.2f})"
        )

    if reasons:
        return Decision.HUMAN_REVIEW, reasons
    return Decision.SURFACE, [
        f"addresses the failure ({addresses:.0%}), scope is tight, test coverage preserved"
    ]


def _looks_like_test(path: str) -> bool:
    from .static import is_test_path

    return is_test_path(path)
