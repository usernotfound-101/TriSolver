"""The splitter is the one stage with no model in it, so it is pinned hard."""

from trisolver.parse import split_failures


def test_splits_fixture_into_one_unit_per_failure(bundle):
    units = split_failures(bundle.raw_log)
    assert len(units) == 5
    assert [u.name for u in units] == [
        "test_discount_applied",
        "test_retry_backoff",
        "Run docker compose pull",
        "Run pip install -r requirements-broken.txt",
        "Run ./.github/scripts/render-notes.sh",
    ]


def test_test_failures_beat_step_failures_in_the_same_job(bundle):
    """The pytest job also emits ##[error]; that must not become a sixth unit."""
    units = split_failures(bundle.raw_log)
    pytest_job = [u for u in units if u.job == "test (3.12)"]
    assert len(pytest_job) == 2
    assert all(u.kind == "test" for u in pytest_job)


def test_timestamps_are_stripped(bundle):
    units = split_failures(bundle.raw_log)
    assert not any("2026-09-26T" in u.log_excerpt for u in units)


def test_signature_prefers_the_real_error_over_the_exit_code(bundle):
    units = {u.name: u for u in split_failures(bundle.raw_log)}
    assert "429 Too Many Requests" in units["Run docker compose pull"].signature
    assert "exit code" not in units["Run docker compose pull"].signature
    assert "did not find expected key" in units["Run ./.github/scripts/render-notes.sh"].signature


def test_implicated_files_come_from_the_traceback(bundle):
    units = {u.name: u for u in split_failures(bundle.raw_log)}
    implicated = units["test_discount_applied"].implicated_files
    assert "tests/test_pricing.py" in implicated
    assert "pricing.py" in implicated


def test_runtime_paths_are_not_implicated():
    log = (
        "=================================== FAILURES ===================================\n"
        "________________________________ test_thing ____________________________________\n"
        '  File "/opt/hostedtoolcache/Python/3.12.6/x64/lib/python3.12/site-packages/x.py", line 3\n'
        "src/app/thing.py:9: AssertionError\n"
    )
    unit = split_failures(log)[0]
    assert unit.implicated_files == ["src/app/thing.py"]


def test_jest_and_go_formats_split_too():
    jest = "  ● Cart › applies discount\n\n    expect(received).toBe(expected)\n      at src/cart.test.ts:14:5\n"
    assert split_failures(jest)[0].name == "Cart › applies discount"

    go = "--- FAIL: TestDiscount (0.01s)\n    pricing_test.go:22: got 4977 want 4978\nFAIL\n"
    assert split_failures(go)[0].name == "TestDiscount"


def test_empty_log_yields_no_units():
    assert split_failures("") == []
    assert split_failures("everything is fine\n") == []
