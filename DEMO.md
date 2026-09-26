# Recording the demo

Target: **3 minutes**. Four beats. Everything below has been run end to end.

## Before you hit record

```bash
cd /home/usercopy/BOBHACK
rm -rf work                      # clean board, no stale briefs or patches
git -C sandbox/storefront checkout -q pricing-integer-cents
git -C sandbox/storefront reset -q --hard
```

Terminal at ~110 columns, dark theme, large font. Colour matters — the
categories are colour-coded and the slides use the same palette.

Two things to know before you speak:

- Jev has **no API key**, so every board prints a `simulated` banner. Do not
  hide it and do not claim live Jev verdicts. Say "the Jev integration is real
  code against their documented endpoint; without a key it falls back to a
  clearly-labelled offline simulator."
- `--live` needs `GITHUB_TOKEN` in `.env` and the network. If the venue Wi-Fi
  is bad, use the fixture (`pricing-pr-482`) instead — same pipeline, same
  five categories, no network.

---

## Beat 1 — the problem (20s)

Show the red run in the browser: four jobs, all failing.

> "Four failing jobs. How many actual problems? That's the question a
> developer spends their morning on, and most of the answer is noise."

## Beat 2 — triage the real run (45s)

```bash
uv run trisolver triage 'https://github.com/usernotfound-101/testrepobob/actions/runs/36258163320' --live
```

Five units, five categories, one action each.

> "It pulled the logs, split them into one unit per failing test or step, and
> asked Jev three typed questions about each: a Choice for category, a Noul for
> whether this PR caused it, and a Score for severity. One regression goes to
> Bob. The dependency pin and the rate-limited registry go to people who own
> them. The flaky test scored 51% — below the floor — so nothing is automated
> and a human looks. That last one is the safety net working, not a miss."

## Beat 3 — the cheat, caught (45s)

```bash
uv run trisolver demo --scenario cheat
```

Let the `BLOCK` sit on screen.

> "Deleting the failing test is the classic way to make CI green. Two
> independent checks catch it: a Jev Noul at 0.95, and a model-free read of the
> diff that just sees a test function disappear. Either one blocks alone."

Optionally follow with `--scenario skip` (a `@pytest.mark.skip`, blocked by a
different signal) and `--scenario sprawl` (correct fix, four files touched,
held for review).

## Beat 4 — Bob fixes the real one (50s)

Do **not** run Bob live: it takes ~2.5 minutes. Show the artefacts.

```bash
cat work/regressions/*.md | head -40        # the brief, incl. CONTRIBUTING
cat work/patches/*.patch                    # Bob's one-line fix
uv run trisolver verify 'https://github.com/usernotfound-101/testrepobob/actions/runs/36258163320' --live --inbox
```

> "Bob got a self-contained brief — the failure, the implicated files, the diff,
> and the repo's own CONTRIBUTING, which says money is integer cents and you
> round once at the end. It changed `int(total)` to `round(total)`. One line.
> Then Jev checked Bob's work before any human saw it: addresses the failure,
> scope is tight, no test weakened. Surfaced."

To regenerate Bob's patch from scratch beforehand (2.5 min, ~$0.40):

```bash
bash sandbox/bob-fix.sh work/regressions/<unit>.md work/patches/<unit>.patch
```

## Close (10s)

> "Sort the red build. Fix only what's real. Check the fix."

---

## Reset between takes

```bash
rm -rf work && git -C sandbox/storefront reset -q --hard
```

Re-running `dispatch` recreates the brief; the patch has to come from Bob again
(or copy the one you kept aside).

## If something goes wrong on camera

- **Empty board** — the run's logs expired or the token lost access. Fall back
  to `uv run trisolver demo --scenario cheat`, which needs no network.
- **`error: Jev request failed`** — only happens with a key set; unset
  `JEV_API_KEY` to fall back to the simulator.
- **Bob hangs** — it blocks on a TTY prompt without `< /dev/null`; the shim
  already handles this, don't invoke `bob run` by hand.
