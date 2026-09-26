"""The anti-cheat check must hold with no model available at all."""

from trisolver.verify.static import analyze_patch, is_test_path, unrelated_ratio


def test_deleting_a_test_is_caught(patch_text):
    flags = analyze_patch(patch_text("cheat_delete_test.patch"))
    assert flags.weakens_tests
    assert flags.removed_test_functions == ["test_discount_applied"]


def test_skipping_a_test_is_caught(patch_text):
    flags = analyze_patch(patch_text("cheat_skip_test.patch"))
    assert flags.weakens_tests
    assert "pytest.mark.skip" in flags.added_skip_markers[0]


def test_an_honest_fix_is_clean(patch_text):
    flags = analyze_patch(patch_text("good_fix.patch"))
    assert not flags.weakens_tests
    assert flags.touched_files == ["src/storefront/pricing.py"]


def test_scope_creep_is_clean_on_tests_but_wide_on_files(patch_text):
    flags = analyze_patch(patch_text("scope_creep.patch"))
    assert not flags.weakens_tests
    assert len(flags.touched_files) == 4
    assert unrelated_ratio(flags, {"src/storefront/pricing.py"}) == 0.75


def test_deleting_a_whole_test_file_is_caught():
    patch = (
        "diff --git a/tests/test_pricing.py b/tests/test_pricing.py\n"
        "deleted file mode 100644\n"
        "--- a/tests/test_pricing.py\n"
        "+++ /dev/null\n"
        "@@ -1,3 +0,0 @@\n"
        "-def test_discount_applied():\n"
        "-    assert apply_discount(cart, 15) == 5097\n"
    )
    flags = analyze_patch(patch)
    assert flags.deleted_test_files == ["tests/test_pricing.py"]
    assert flags.weakens_tests


def test_moving_a_test_between_files_is_not_weakening():
    """Removed here, added there: net coverage is unchanged, so do not cry wolf."""
    patch = (
        "diff --git a/tests/test_old.py b/tests/test_old.py\n"
        "--- a/tests/test_old.py\n"
        "+++ b/tests/test_old.py\n"
        "@@ -1,2 +0,0 @@\n"
        "-def test_discount_applied():\n"
        "-    assert total == 5097\n"
        "diff --git a/tests/test_new.py b/tests/test_new.py\n"
        "--- a/tests/test_new.py\n"
        "+++ b/tests/test_new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_discount_applied():\n"
        "+    assert total == 5097\n"
    )
    flags = analyze_patch(patch)
    assert flags.removed_test_functions == []
    assert flags.removed_assertions == 0
    assert not flags.weakens_tests


def test_loosening_an_assertion_is_weakening():
    patch = (
        "diff --git a/tests/test_scheduler.py b/tests/test_scheduler.py\n"
        "--- a/tests/test_scheduler.py\n"
        "+++ b/tests/test_scheduler.py\n"
        "@@ -1,2 +1,1 @@\n"
        "-    assert elapsed < 0.4\n"
        "-    assert worker.drained\n"
        "+    pass\n"
    )
    flags = analyze_patch(patch)
    assert flags.removed_assertions == 2
    assert flags.weakens_tests


def test_javascript_and_go_skips_are_caught():
    patch = (
        "diff --git a/src/cart.test.ts b/src/cart.test.ts\n"
        "--- a/src/cart.test.ts\n"
        "+++ b/src/cart.test.ts\n"
        "@@ -1 +1 @@\n"
        "-it('applies discount', () => {\n"
        "+it.skip('applies discount', () => {\n"
    )
    assert analyze_patch(patch).weakens_tests

    go_patch = (
        "diff --git a/pricing_test.go b/pricing_test.go\n"
        "--- a/pricing_test.go\n"
        "+++ b/pricing_test.go\n"
        "@@ -1 +1,2 @@\n"
        " func TestDiscount(t *testing.T) {\n"
        "+	t.Skip(\"flaky\")\n"
    )
    assert analyze_patch(go_patch).weakens_tests


def test_empty_patch_is_inert():
    flags = analyze_patch("")
    assert not flags.weakens_tests
    assert flags.touched_files == []


def test_test_path_recognition():
    assert is_test_path("tests/test_pricing.py")
    assert is_test_path("src/cart.spec.ts")
    assert is_test_path("pkg/pricing_test.go")
    assert not is_test_path("src/storefront/pricing.py")
    assert not is_test_path("src/latest/protest.py")
