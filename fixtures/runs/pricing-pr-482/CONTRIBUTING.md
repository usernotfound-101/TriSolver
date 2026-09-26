# Contributing to storefront

## Money

All monetary values are integer cents. Never use floats for money. When a
percentage must be applied, round once, at the end, with `round()` on the
cent value -- banker's rounding is what the finance team reconciles against.

## Tests

A red test is information. Do not delete, rename, or skip a test to make CI
green. Quarantining a genuinely flaky test is allowed, but only via the
`@pytest.mark.flaky` decorator **and** a linked issue in the same PR.

Loosening an assertion (widening a tolerance, swapping `==` for `approx`)
counts as deleting the test unless the PR explains why the old bound was
wrong.

## Scope

One concern per pull request. Formatting sweeps, import reordering, and
drive-by refactors belong in their own PR so review stays cheap.
