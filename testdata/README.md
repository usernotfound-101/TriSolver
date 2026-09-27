# sandbox — a repo that goes red on purpose

A throwaway GitHub repository used to test TriSolver against a real Actions
runner instead of recorded fixtures. **This directory is gitignored** by the
TriSolver repo; it will not survive a fresh clone.

## What it is

`storefront/` is a small, genuinely working Python service with a CI workflow
of four jobs. Three of them fail on `main` already, the way a real repo's CI is
usually already a bit red:

| job | fails with | category |
| --- | --- | --- |
| `test (3.12)` | `test_retry_backoff` — a wall-clock assertion, with a retry in the captured log | **flaky** |
| `integration` | a real `429 Too Many Requests` from the network | **infra** |
| `deps (windows-latest)` | `pip install -r requirements-broken.txt` → real `ResolutionImpossible` | **dependency** |
| `release-notes` | PyYAML refusing `config/release.yml` | **config** |

The pull request then adds one thing on top: `apply_discount` switches from
`round()` to `int()`, which loses a cent. `test_discount_applied` goes red.

| | fails with | category |
| --- | --- | --- |
| `test (3.12)` | `assert 4977 == 4978` | **regression** |

So the PR's run is red across all five categories, and exactly one of them is
attributable to the diff. That is the case worth testing: TriSolver should
dispatch the pricing regression to Bob and route the other four elsewhere
without touching an agent.

The arithmetic is real, not staged — at 17% off 5997 cents the total is
4977.51, so `round()` gives 4978 and `int()` gives 4977. (At 15% they agree,
which is why the percentage is 17.)

`test_retry_backoff` fails every time rather than intermittently. A genuinely
non-deterministic test would make the sandbox unreproducible; this one fails
the *way* a flaky test fails — timing assertion, retry in the log — which is
what the triage step reads.

## Push it

```bash
./push.sh <your-github-username>            # needs the gh CLI, authenticated
```

That creates a private repo, pushes `main`, branches
`pricing-integer-cents`, applies the regression, opens the PR, waits for CI,
and prints the run URL.

### Without the gh CLI

```bash
cd storefront
git init -b main && git add -A && git commit -m "storefront: initial service"
# create an empty private repo on github.com, then:
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main

git checkout -b pricing-integer-cents
#   in src/storefront/pricing.py: return round(total)  ->  return int(total)
#   in src/storefront/cart.py:    add `note: str | None = None` to Item
git commit -am "pricing: switch discount maths to integer cents"
git push -u origin pricing-integer-cents
# open the PR in the web UI, wait for CI, copy the run URL
```

## Point TriSolver at it

From the TriSolver repo root, with `GITHUB_TOKEN` set in `.env`:

```bash
uv run trisolver triage   'https://github.com/<you>/<repo>/actions/runs/<id>' --live
uv run trisolver dispatch 'https://github.com/<you>/<repo>/actions/runs/<id>' --live
uv run trisolver run      'https://github.com/<you>/<repo>/actions/runs/<id>' --live --report
```

Live ingestion downloads the run's log archive, keeps only the failed jobs,
pulls the PR diff, and picks up `storefront/CONTRIBUTING.md` — whose money and
scope rules the verification gates then check Bob's patch against.

## What to watch for

- The five units should split cleanly, one per failing test or step.
- `caused_by_pr` should be high only for `test_discount_applied`.
- Only that one should route to Bob.
- Hand Bob the brief, and if it deletes or skips `test_discount_applied`
  instead of restoring `round()`, verification must block it.

## Token scopes

A fine-grained PAT on this one repo needs **Actions: read**, **Pull requests:
read**, **Contents: read**. A classic PAT needs `repo`. Put it in the TriSolver
repo's `.env` as `GITHUB_TOKEN`.

---

## IBM Bob evidence

- `bob-fix.sh` — the shim TriSolver's `--bob cli` adapter calls. Runs
  `bob run --mode agent` headless on one brief and snapshots the diff.
- `../submission/bob-real-patch.patch` — the patch Bob actually produced.
- `../submission/bob-usage-statement.txt` — what Bob did, from the
  stream-json transcript (task 3ed0eeca653849ebf227e3d6f843baaf,
  149 s, 13 tool calls, 0.40 USD).
