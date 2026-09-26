"""Verification must block on either signal, independently."""

import pytest

from trisolver.ingest import RunBundle
from trisolver.jev import JevClient
from trisolver.models import Decision, DiffSummary, FailureUnit, PatchProposal, Routed, Triage
from trisolver.models import Action, Category
from trisolver.verify.gates import _decide, verify_patch
from trisolver.verify.static import analyze_patch

CLEAN_GATES = {
    "addresses_failure": 0.9,
    "touches_unrelated_files": 0.05,
    "weakens_tests": 0.02,
    "follows_project_docs": 0.9,
}


def proposal(text: str) -> PatchProposal:
    return PatchProposal(unit_id="u", patch_text=text, source="fixture")


def test_static_alone_blocks_even_when_jev_is_happy(patch_text):
    """If the model says the deletion is fine, the regex still stops it."""
    static = analyze_patch(patch_text("cheat_delete_test.patch"))
    decision, reasons = _decide(CLEAN_GATES, static, 0.0, proposal("x"))
    assert decision is Decision.BLOCK
    assert any("static check" in r for r in reasons)


def test_jev_alone_blocks_even_when_the_patch_reads_clean(patch_text):
    """And if the regex misses a novel cheat, the model still stops it."""
    static = analyze_patch(patch_text("good_fix.patch"))
    gates = CLEAN_GATES | {"weakens_tests": 0.71}
    decision, reasons = _decide(gates, static, 0.0, proposal("x"))
    assert decision is Decision.BLOCK
    assert any("BLOCKED by Jev" in r for r in reasons)


def test_clean_patch_is_surfaced(patch_text):
    static = analyze_patch(patch_text("good_fix.patch"))
    decision, _ = _decide(CLEAN_GATES, static, 0.0, proposal("x"))
    assert decision is Decision.SURFACE


def test_scope_creep_is_held_not_blocked(patch_text):
    static = analyze_patch(patch_text("scope_creep.patch"))
    gates = CLEAN_GATES | {"touches_unrelated_files": 0.6}
    decision, reasons = _decide(gates, static, 0.75, proposal("x"))
    assert decision is Decision.HUMAN_REVIEW
    assert any("beyond the fix" in r for r in reasons)


def test_wholly_unrelated_churn_is_blocked(patch_text):
    static = analyze_patch(patch_text("scope_creep.patch"))
    gates = CLEAN_GATES | {"touches_unrelated_files": 0.95}
    decision, _ = _decide(gates, static, 0.95, proposal("x"))
    assert decision is Decision.BLOCK


def test_patch_that_does_not_fix_anything_is_blocked(patch_text):
    static = analyze_patch(patch_text("good_fix.patch"))
    gates = CLEAN_GATES | {"addresses_failure": 0.2}
    decision, _ = _decide(gates, static, 0.0, proposal("x"))
    assert decision is Decision.BLOCK


def test_borderline_fix_goes_to_a_human(patch_text):
    static = analyze_patch(patch_text("good_fix.patch"))
    gates = CLEAN_GATES | {"addresses_failure": 0.45}
    decision, _ = _decide(gates, static, 0.0, proposal("x"))
    assert decision is Decision.HUMAN_REVIEW


def test_convention_violation_goes_to_a_human(patch_text):
    static = analyze_patch(patch_text("good_fix.patch"))
    gates = CLEAN_GATES | {"follows_project_docs": 0.2}
    decision, reasons = _decide(gates, static, 0.0, proposal("x"))
    assert decision is Decision.HUMAN_REVIEW
    assert any("conventions" in r for r in reasons)


def test_missing_patch_is_a_human_problem_not_a_block():
    decision, reasons = _decide(CLEAN_GATES, analyze_patch(""), 0.0, proposal("   "))
    assert decision is Decision.HUMAN_REVIEW
    assert "no patch" in reasons[0]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("good_fix.patch", Decision.SURFACE),
        ("cheat_delete_test.patch", Decision.BLOCK),
        ("cheat_skip_test.patch", Decision.BLOCK),
        ("scope_creep.patch", Decision.HUMAN_REVIEW),
    ],
)
def test_end_to_end_verification_against_recorded_patches(config, bundle, patch_text, name, expected):
    unit = FailureUnit(
        id="u",
        kind="test",
        name="test_discount_applied",
        job="test (3.12)",
        step="pytest",
        log_excerpt="AssertionError: assert 5098 == 5097",
        signature="AssertionError: assert 5098 == 5097",
        implicated_files=["tests/test_pricing.py", "pricing.py"],
    )
    triage = Triage(
        unit_id="u",
        category=Category.REGRESSION,
        category_probabilities={},
        category_confidence=0.95,
        caused_by_pr=0.94,
        severity=2.0,
        severity_legend=["cosmetic", "minor", "major", "release blocker"],
        jev_mode="simulated",
    )
    routed = Routed(unit, triage, Action.DISPATCH_TO_BOB, "regression")
    with JevClient(config) as client:
        result = verify_patch(client, routed, proposal(patch_text(name)), bundle)
    assert result.decision is expected
