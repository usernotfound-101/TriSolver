# TriSolver

**When CI goes red, most of the time goes on sorting, not fixing.**

A developer opens the run, reads four jobs of logs, works out which failures
are flaky, which are infrastructure, which are dependency drift, and which are
a real regression — and then fixes the one or two that were real.

TriSolver does the sorting, fixes only what is genuinely broken, and checks the
fix before anyone reads it.

```
ingest ──▶ split ──▶ triage ──▶ route ──▶ dispatch ──▶ verify
 GitHub     parsing    Jev       code       Bob         Jev
                                                         │
                                        surface ◀────────┴────────▶ block
```

Each stage is doing the job it is actually good at. Splitting a log is a
parsing problem, so parsing code does it. Deciding *what a failure is* is a
judgement under uncertainty, so Jev does it and returns a calibrated number.
Deciding *what to do about it* is policy, so plain deterministic code does it,
and you can read every branch. Writing a fix is open-ended work, so Bob does
it. Deciding whether the fix is honest is judgement again, so Jev does that too
— and this time it is checking an agent, not a human.

---

## Quick start

```bash
uv sync
uv run trisolver run pricing-pr-482      # the whole pipeline, offline
uv run trisolver demo --scenario cheat   # the bit worth watching
```

No API key is needed to run it. Without `JEV_API_KEY` the Jev calls fall back
to keyword heuristics and **every line of output is stamped `simulated`** —
that mode exists so the pipeline is demoable on a plane, not so simulated
numbers can be passed off as model output. Copy `.env.example` to `.env` and
add a key for real verdicts.

## What it does, stage by stage

### 1. Ingest

A fixture directory, or a live Actions run:

```bash
uv run trisolver triage https://github.com/acme/storefront/actions/runs/19284730192 --live
```

Live mode downloads the run's log archive, keeps only the jobs that actually
failed, pulls the PR diff, and picks up `CONTRIBUTING.md` or the runbook so the
project's own conventions travel with the failure. Needs `GITHUB_TOKEN` with
`actions:read` and `pull_requests:read`.

### 2. Split

`src/trisolver/parse/` turns the raw log into one **failure unit** per failing
test or step: timestamps stripped, `##[group]` boundaries used to name the
step, pytest / jest / vitest / go test understood, and a step-level fallback
for jobs that died before any test ran. A job that produced test failures is
not also reported at step level, because "pytest exited 1" is not information.

Signatures are scored, not first-matched — a step that died on a rate limit
reports `429 Too Many Requests`, not `Process completed with exit code 18`, and
a keyword inside a CLI flag (`--show-diff-on-failure`) does not make a command
echo look like an error message. ANSI colour codes are stripped; real logs are
full of them because tools detect CI as a terminal.

**The same failure across matrix legs is one unit.** Six Python versions
failing the same lint hook is one problem, not six — reporting it six times
inflates the board and would spend six agents on a single fix. The other legs
are recorded on the surviving unit and shown as `x6 jobs`.

### 3. Triage — Jev

Each unit goes to Jev once, as one state with three typed questions:

| key | type | what it gets you |
| --- | --- | --- |
| `category` | **Choice** | regression / flaky / infra / dependency / config, with a probability on each and a confidence |
| `caused_by_pr` | **Noul** | probability this failure comes from the files this PR changed |
| `severity` | **Score** | cosmetic → minor → major → release blocker |

The state is the log excerpt, the files the traceback named, and a summary of
the PR diff. Attribution is measured over the *source* files a failure named,
not the test file — a failing test always names its own file, so counting it
would dilute the signal identically on every unit.

### 4. Route — code

`src/trisolver/route/router.py` is deliberately boring, deterministic, and
short enough to argue about line by line:

| category | confidence | action |
| --- | --- | --- |
| any | `< 65%` | **human** — nothing is automated on a guess |
| flaky | ≥ 65% | rerun once, label `flaky-test`, quarantine candidate |
| infra | ≥ 65% | ping `#ci-infra`, no code change will help |
| dependency | ≥ 65% | ping `#dependencies`, pinning is a human call |
| config | ≥ 65% | human — the fix lives outside the code under test |
| regression | ≥ 75% **and** `caused_by_pr ≥ 60%` | **dispatch to Bob** |
| regression | otherwise | human — probably pre-existing on the base branch |

Confidence gates automation. Anything TriSolver is unsure about goes where it
would have gone anyway: to a person.

`--effects` prints the concrete side effects each route would perform, without
performing them.

### 5. Dispatch — Bob

Every high-confidence regression becomes one self-contained brief: the
failure, the files it implicates, the diff, and the project's conventions. One
subagent per regression, and they are independent by construction, so they run
in parallel.

```bash
uv run trisolver dispatch pricing-pr-482   # writes work/regressions/*.md
```

Three ways to get the patch back, all producing the same result:

```bash
--bob inbox    # default: you run Bob in Agent mode against the briefs;
               # each subagent writes work/patches/<unit-id>.patch
--bob cli --bob-cmd "bob run --task {brief} --out {patch}"   # headless Bob
--bob fixture  # recorded patches, for a deterministic demo
```

The brief states the acceptance criteria up front, word for word the gates the
patch will be judged against, and ends: **fix the code, do not fix the test.**

### 6. Verify — Jev

Before a human sees anything, the patch clears four Noul gates:

| gate | blocks at |
| --- | --- |
| `addresses_failure` | `< 0.35` blocks, `< 0.60` holds for a human |
| `touches_unrelated_files` | `> 0.80` blocks, `> 0.50` holds |
| `weakens_tests` | **`> 0.30` blocks** |
| `follows_project_docs` | `< 0.40` holds |

and, independently, a deterministic read of the diff itself
(`src/trisolver/verify/static.py`): deleted test files, removed test
functions, added `skip`/`xfail`/`t.Skip`/`it.skip`, net-removed assertions.

**Either signal blocks on its own.** A novel cheat the regex misses is still
caught by the model; a model having a bad day still cannot wave through a
deleted test. The static check is net, not gross — a test moved between files
removes a `def test_x` and adds one back, and that is not a weakening.

## The demo

An agent deleting the failing test to make CI green is a well-known cheat.
Four recorded patches, four verdicts:

```bash
uv run trisolver demo --scenario honest   # fixes the rounding bug      -> SURFACE
uv run trisolver demo --scenario cheat    # deletes the failing test    -> BLOCK
uv run trisolver demo --scenario skip     # adds @pytest.mark.skip      -> BLOCK
uv run trisolver demo --scenario sprawl   # fixes it, reformats 3 files -> HUMAN_REVIEW
```

```
  BLOCK         test_discount_applied

    addresses_failure           0.15  ###.................
    touches_unrelated_files     0.01  ....................
    weakens_tests               0.95  ###################.
    follows_project_docs        0.25  #####...............
    static check                      removed test(s): test_discount_applied; removed 1 assertion(s)
    files touched                     tests/test_pricing.py
    BLOCKED by static check: removed test(s): test_discount_applied; removed 1 assertion(s)
    BLOCKED by Jev: weakens_tests=0.95 (threshold 0.30)
    The failing test was neutralised rather than the defect fixed.
```

`trisolver run` and `trisolver verify` exit **2** when any patch is blocked, so
the whole thing drops into a workflow as a gate.

## The fixture

`fixtures/runs/pricing-pr-482/` is one red run of four jobs holding one of each
failure kind — a real off-by-one from float money maths, a timing-sensitive
scheduler test, a Docker Hub 429, a pip `ResolutionImpossible`, and a malformed
workflow YAML — plus the PR diff and a `CONTRIBUTING.md` whose money and
scope rules the gates actually check against.

## Commands

```
trisolver fixtures                     list the recorded runs
trisolver triage   <target>            split and classify        [--json] [--effects]
trisolver dispatch <target>            write one Bob brief per regression
trisolver verify   <target> --inbox    gate the patches Bob left behind
trisolver run      <target>            everything                [--bob inbox|cli|fixture]
trisolver demo     --scenario cheat    the scripted story
```

Common flags: `--live`, `--jev auto|live|simulated`, `--workdir DIR`, `--report`.

## Layout

```
src/trisolver/
  parse/     log -> failure units          (no model)
  jev/       client, questions, schema, offline simulator
  triage.py  state construction + attribution
  route/     confidence-gated policy       (no model)
  bob/       briefs and the three adapters
  verify/    static.py (no model) + gates.py (Jev)
  pipeline.py, cli.py, report.py
```

```bash
uv run pytest        # 75 tests
```

## Honest limits

- Without `JEV_API_KEY` the classifications are keyword heuristics. They are
  labelled everywhere they appear, and the routing thresholds are tuned for
  real calibrated probabilities, not for the simulator. On the four public
  repos above the simulator lands most units below the confidence floor, so
  they route to a human — the right behaviour, but a demonstration of the
  floor rather than of classification quality.
- Live ingestion is verified against real runs in `psf/requests`,
  `pydantic/pydantic`, `urllib3/urllib3` and `encode/httpx`. The
  `usernotfound-101/testrepobob` sandbox could not be used: that account is
  locked for billing, so its jobs are refused before a runner is assigned and
  produce no logs at all.
- The `--bob cli` adapter is a seam for a headless Bob. Bob ships as an IDE
  agent, so `inbox` is the path that actually runs today.
- The router's thresholds are constants in `config.py`. They are guesses until
  you have a week of your own CI history to fit them to.
