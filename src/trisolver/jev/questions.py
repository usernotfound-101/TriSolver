"""The typed questions TriSolver asks Jev.

Two sets, asked at two different moments:

* ``TRIAGE_QUESTIONS`` -- asked once per failure unit, before any fixing.
* ``VERIFY_QUESTIONS`` -- asked once per proposed patch, before a human sees it.

Question keys are stable identifiers: the same key is used to find the answer
in the response, and the router and gates index on them by name.
"""

from __future__ import annotations

# Ordered severity rubric. Index i corresponds to criteria key str(i), so a
# returned score of 2.4 sits between "major" and "release blocker".
SEVERITY_LEGEND = ["cosmetic", "minor", "major", "release blocker"]

TRIAGE_QUESTIONS: dict[str, dict] = {
    "category": {
        "type": "choice",
        "instructions": (
            "A CI job failed. Given the log excerpt for this one failure and a summary "
            "of the pull request diff, which single category best explains why it failed?"
        ),
        "criteria": {
            "regression": (
                "Product or test code is genuinely wrong. The assertion, exception, or "
                "compile error reflects real behaviour that changed or broke."
            ),
            "flaky": (
                "The test is non-deterministic. Timing, ordering, sleeps, races, ports, "
                "clocks, randomness, or a retry that passed on a rerun."
            ),
            "infra": (
                "The CI environment failed, not the code. Runner died, disk full, network "
                "unreachable, registry 5xx or 429, cache restore failed, OOM kill, timeout "
                "of the job itself."
            ),
            "dependency": (
                "A third-party package changed or cannot be resolved. Version conflict, "
                "yanked release, lockfile drift, new major of a transitive dependency."
            ),
            "config": (
                "The workflow, build, or project configuration is malformed or wrong. Bad "
                "YAML, missing secret or env var, wrong matrix entry, misconfigured tool."
            ),
        },
    },
    "caused_by_pr": {
        "type": "noul",
        "instructions": (
            "Was this failure caused by the files changed in this pull request? Judge only "
            "from whether the changed files plausibly reach the failing code path."
        ),
        "criteria": {
            "true": "The diff touches code on the path to this failure.",
            "false": "The failure is unrelated to the diff, or pre-existing on the base branch.",
        },
    },
    "severity": {
        "type": "score",
        "instructions": (
            "How severe is this failure for the team, assuming it is real? Weigh blast "
            "radius and whether it blocks a release, not how hard it is to fix."
        ),
        "criteria": {
            "0": "Cosmetic. A log line, a lint nit, or a message-only difference.",
            "1": "Minor. One narrow case is wrong; the feature broadly works.",
            "2": "Major. A user-visible feature is broken or produces wrong results.",
            "3": "Release blocker. Data loss, security, money, or the build is unusable.",
        },
    },
}

VERIFY_QUESTIONS: dict[str, dict] = {
    "addresses_failure": {
        "type": "noul",
        "instructions": (
            "Here is a CI failure and a proposed patch. Does the patch actually address "
            "the cause of this failure, so the test would pass for the right reason?"
        ),
        "criteria": {
            "true": "The change fixes the underlying defect the failure exposed.",
            "false": "The change is unrelated, cosmetic, or silences the symptom only.",
        },
    },
    "touches_unrelated_files": {
        "type": "noul",
        "instructions": (
            "Does the patch modify files that are not needed to fix this specific failure? "
            "Drive-by refactors, formatting sweeps, and unrelated features all count."
        ),
        "criteria": {
            "true": "The patch changes files or code beyond what this fix requires.",
            "false": "Every change in the patch is needed for this fix.",
        },
    },
    "weakens_tests": {
        "type": "noul",
        "instructions": (
            "Does the patch make the test suite weaker instead of fixing the code? Deleting "
            "a test, renaming it out of collection, adding skip or xfail, loosening an "
            "assertion, widening a tolerance, or catching the exception it should raise all "
            "count as weakening."
        ),
        "criteria": {
            "true": "The patch removes, skips, or weakens test coverage.",
            "false": "Test coverage is preserved or strengthened.",
        },
    },
    "follows_project_docs": {
        "type": "noul",
        "instructions": (
            "Given the project's CONTRIBUTING notes or runbook excerpt, does the patch "
            "follow the conventions stated there?"
        ),
        "criteria": {
            "true": "The patch respects the project's stated conventions.",
            "false": "The patch violates a stated convention, or no convention applies.",
        },
    },
}
