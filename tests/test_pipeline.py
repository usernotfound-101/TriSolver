"""End-to-end wiring, offline."""

import json

from trisolver.cli import main
from trisolver.ingest import summarize_diff
from trisolver.jev import JevClient
from trisolver.models import Action, Decision
from trisolver.pipeline import fix_stage, triage_stage
from trisolver.triage import diff_overlap
from trisolver.parse import split_failures


def test_diff_summary_counts_files_and_lines(bundle):
    assert bundle.diff.files == ["src/storefront/pricing.py", "src/storefront/cart.py"]
    assert bundle.diff.added_lines > bundle.diff.removed_lines > 0


def test_empty_diff_summarizes_to_nothing():
    summary = summarize_diff("")
    assert summary.files == [] and summary.added_lines == 0


def test_attribution_ignores_the_failing_test_file(bundle):
    """tests/test_pricing.py is not in the diff; pricing.py is. That is a hit."""
    unit = next(u for u in split_failures(bundle.raw_log) if u.name == "test_discount_applied")
    assert diff_overlap(unit, bundle.diff) == 1.0


def test_attribution_is_zero_for_an_unrelated_failure(bundle):
    unit = next(u for u in split_failures(bundle.raw_log) if u.name == "test_retry_backoff")
    assert diff_overlap(unit, bundle.diff) == 0.0


def test_triage_stage_routes_every_unit(config, bundle):
    result, client = triage_stage(config, "pricing-pr-482")
    client.close()
    assert len(result.routed) == 5
    assert {r.action for r in result.routed} == {
        Action.DISPATCH_TO_BOB,
        Action.RERUN_AND_QUARANTINE,
        Action.PING_INFRA,
        Action.PING_DEPS,
        Action.HUMAN_REVIEW,
    }
    assert len(result.regressions) == 1
    assert result.regressions[0].unit.name == "test_discount_applied"


def test_inbox_mode_writes_a_brief_and_waits(config):
    result, client = triage_stage(config, "pricing-pr-482")
    result = fix_stage(config, result, client, bob_mode="inbox")
    client.close()

    assert len(result.briefs) == 1
    brief = result.briefs[0].read_text(encoding="utf-8")
    assert "Fix the code. Do not fix the test." in brief
    assert "AssertionError: assert 5098 == 5097" in brief
    assert "integer cents" in brief  # CONTRIBUTING was handed to Bob
    assert result.pending == [result.regressions[0].unit.id]
    assert result.verifications == {}


def test_inbox_mode_picks_up_a_patch_left_behind(config, patch_text):
    result, client = triage_stage(config, "pricing-pr-482")
    unit_id = result.regressions[0].unit.id
    config.patches_dir.mkdir(parents=True, exist_ok=True)
    (config.patches_dir / f"{unit_id}.patch").write_text(patch_text("good_fix.patch"))

    result = fix_stage(config, result, client, bob_mode="inbox")
    client.close()
    assert result.verifications[unit_id].decision is Decision.SURFACE


def test_cli_demo_blocks_the_cheat(config, capsys, tmp_path):
    exit_code = main(["demo", "--scenario", "cheat", "--workdir", str(tmp_path), "--jev", "simulated"])
    output = capsys.readouterr().out
    assert exit_code == 2  # non-zero: a blocked patch must fail a CI gate
    assert "BLOCK" in output
    assert "removed test(s): test_discount_applied" in output


def test_cli_demo_surfaces_the_honest_fix(capsys, tmp_path):
    exit_code = main(["demo", "--scenario", "honest", "--workdir", str(tmp_path), "--jev", "simulated"])
    assert exit_code == 0
    assert "SURFACE" in capsys.readouterr().out


def test_cli_triage_json_is_machine_readable(capsys, tmp_path):
    assert main(["triage", "pricing-pr-482", "--json", "--workdir", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 5
    assert payload[0]["action"] == "dispatch_to_bob"


def test_cli_writes_a_markdown_report(capsys, tmp_path):
    main(["run", "--bob", "fixture", "--fixture-map", _map(tmp_path), "pricing-pr-482",
          "--workdir", str(tmp_path), "--report"])
    report = (tmp_path / "reports" / "pricing-pr-482.md").read_text(encoding="utf-8")
    assert "# TriSolver report" in report
    assert "| regression |" in report


def _map(tmp_path) -> str:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"test_discount_applied": "good_fix.patch"}))
    return str(path)


def test_unknown_fixture_reports_what_exists(capsys, tmp_path):
    assert main(["triage", "nope", "--workdir", str(tmp_path)]) == 1
    assert "pricing-pr-482" in capsys.readouterr().err
