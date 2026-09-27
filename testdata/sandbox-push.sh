#!/usr/bin/env bash
# Publish the sandbox repo and create the branch whose CI run TriSolver triages.
#
#   ./push.sh [remote-url]
#
# Works with plain git over SSH; the GitHub CLI is optional. If gh is present
# it also opens the PR and waits for the run. If not, it prints the URL to
# open the PR by hand.
#
# Leaves you with:
#   main                   -- pricing is correct; integration/deps/release-notes
#                             already fail, the way a real repo's CI already does
#   pricing-integer-cents  -- one real regression on top
#
# The PR's run therefore goes red across all five categories, and only one is
# attributable to the diff. That is the case worth testing.

set -euo pipefail

REMOTE="${1:-git@github.com:usernotfound-101/testrepobob.git}"
BRANCH="pricing-integer-cents"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/storefront"

# git@github.com:owner/repo.git | https://github.com/owner/repo(.git) -> owner/repo
# Falls back to the last two path segments, which also makes the script
# testable against a local bare repo.
SLUG="$(printf '%s' "$REMOTE" | sed -E 's#\.git$##; s#^git@[^:]+:##; s#^[a-z]+://[^/]+/##')"
SLUG="$(printf '%s' "$SLUG" | awk -F/ '{n=NF; if (n>=2) printf "%s/%s", $(n-1), $n; else printf "%s", $0}')"
if [[ ! "$SLUG" =~ ^[^/]+/[^/]+$ ]]; then
  echo "error: could not read owner/repo out of '$REMOTE'" >&2
  exit 64
fi

echo "==> repo:   $SLUG"
echo "==> remote: $REMOTE"

if ! git ls-remote "$REMOTE" >/dev/null 2>&1; then
  echo "error: cannot reach $REMOTE. Check the repo exists and your SSH key is loaded:" >&2
  echo "         ssh -T git@github.com" >&2
  exit 1
fi

if [[ -n "$(git ls-remote --heads "$REMOTE" 2>/dev/null)" ]]; then
  echo "warning: $SLUG already has branches. This script force-pushes $BRANCH."
  read -r -p "continue? [y/N] " reply
  [[ "$reply" == [yY] ]] || exit 0
fi

cd "$SRC"

# ---------------------------------------------------------------- main branch
if [[ ! -d .git ]]; then
  git init -q -b main
  git add -A
  git -c commit.gpgsign=false commit -q -m "storefront: initial service

Pricing rounds once at the end, per CONTRIBUTING. The integration, deps and
release-notes jobs fail on main already -- a rate-limited registry, an
unsatisfiable pin, and a malformed release config -- which is the pre-existing
CI noise TriSolver has to sort out from a real regression."
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"

echo "==> pushing main"
git checkout -q main
git push -q -u origin main

# ------------------------------------------------------------- the regression
echo "==> branching $BRANCH and introducing the regression"
git checkout -q -B "$BRANCH" main

python3 - <<'PY'
from pathlib import Path

pricing = Path("src/storefront/pricing.py")
text = pricing.read_text()
assert "return round(total)" in text, "pricing.py is not in its baseline state"
pricing.write_text(
    text.replace(
        '''def apply_discount(cart: Cart, percent: float) -> int:
    """Return the cart total in cents after a percentage discount."""''',
        '''def apply_discount(cart: Cart, percent: float) -> int:
    """Return the cart total in cents after a percentage discount.

    Discounts are computed in integer cents so repeated application cannot
    drift, per the pricing RFC.
    """''',
    ).replace("    return round(total)", "    return int(total)")
)

cart = Path("src/storefront/cart.py")
if "note: str | None" not in cart.read_text():
    cart.write_text(cart.read_text().replace("    qty: int = 1", "    qty: int = 1\n    note: str | None = None"))
PY

git add -A
git -c commit.gpgsign=false commit -q -m "pricing: switch discount maths to integer cents

Compute the discount in cents and truncate, so repeated application cannot
drift. Also carries an optional per-line note on Item."

git push -q -f -u origin "$BRANCH"
git checkout -q main   # leave the worktree on the clean baseline

echo
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh pr view "$BRANCH" >/dev/null 2>&1 || gh pr create --repo "$SLUG" \
    --base main --head "$BRANCH" \
    --title "pricing: switch discount maths to integer cents" \
    --body "Computes the discount in cents and truncates rather than rounding at the end."
  echo "==> waiting for CI (a couple of minutes)"
  sleep 20
  RUN_ID="$(gh run list --repo "$SLUG" --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId')"
  gh run watch --repo "$SLUG" "$RUN_ID" --exit-status >/dev/null 2>&1 || true
  RUN_URL="$(gh run list --repo "$SLUG" --branch "$BRANCH" --limit 1 --json url -q '.[0].url')"
  echo
  echo "==> run: $RUN_URL"
  echo
  echo "Then, from the TriSolver repo root:"
  echo "    uv run trisolver triage '$RUN_URL' --live"
else
  cat <<MSG
==> pushed. Two steps left, by hand (no gh CLI here):

  1. Open the pull request:
       https://github.com/$SLUG/compare/main...$BRANCH?expand=1

  2. Wait for CI, then copy the run URL from:
       https://github.com/$SLUG/actions?query=branch%3A$BRANCH

Then, from the TriSolver repo root, with GITHUB_TOKEN set in .env:

     uv run trisolver triage '<run-url>' --live
     uv run trisolver run    '<run-url>' --live --report
MSG
fi
