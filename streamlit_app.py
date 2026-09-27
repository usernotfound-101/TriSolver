"""TriSolver on the web.

This is not a mockup. It imports the real pipeline and runs it in the browser
against a recorded GitHub Actions run, then puts real patches through the real
verification gates. The only thing standing in is Jev itself: with no API key
the client falls back to its labelled offline simulator, which is stated on
the page rather than hidden.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))

from trisolver.config import (  # noqa: E402
    MAX_UNRELATED_FILES,
    MAX_WEAKENS_TESTS,
    MIN_ADDRESSES_FAILURE,
    MIN_CATEGORY_CONFIDENCE,
    Config,
)
from trisolver.jev import JevClient  # noqa: E402
from trisolver.models import Action, Decision, PatchProposal  # noqa: E402
from trisolver.pipeline import triage_stage  # noqa: E402
from trisolver.route.actions import plan_effects  # noqa: E402
from trisolver.verify import verify_patch  # noqa: E402

st.set_page_config(page_title="TriSolver", page_icon="🚦", layout="wide")

CATEGORY_COLOUR = {
    "regression": "#F2545B", "flaky": "#D9A036", "infra": "#5AA9FF",
    "dependency": "#B584F5", "config": "#3AC4CE",
}
VERDICT_COLOUR = {
    Decision.SURFACE: "#42B77A", Decision.BLOCK: "#F2545B",
    Decision.HUMAN_REVIEW: "#D9A036",
}
ACTION_LABEL = {
    Action.DISPATCH_TO_BOB: "→ bob", Action.RERUN_AND_QUARANTINE: "rerun + quarantine",
    Action.PING_INFRA: "ping infra", Action.PING_DEPS: "ping deps",
    Action.HUMAN_REVIEW: "human",
}

PATCHES = {
    "Bob's real fix": ("submission/bob-real-patch.patch",
                       "What IBM Bob actually produced for this failure."),
    "deletes the failing test": ("fixtures/patches/cheat_delete_test.patch",
                                 "The classic way to make CI green."),
    "adds @pytest.mark.skip": ("fixtures/patches/cheat_skip_test.patch",
                               "A quieter version of the same cheat."),
    "fixes it, but touches 4 files": ("fixtures/patches/scope_creep.patch",
                                      "Correct fix buried in unrelated churn."),
}

st.markdown("""
<style>
  .stApp { background:#0F1319; }
  .block-container { padding-top:2.2rem; max-width:1180px; }
  h1,h2,h3,p,li,span,div { color:#E3E8EF; }
  code, .mono { font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
  .rule { width:60px;height:3px;background:#F2545B;border-radius:2px;margin-bottom:16px; }
  .tagline { color:#8B95A5; font-size:1.15rem; margin-top:-.6rem; }
  .card { background:#171C24;border:1px solid #2A313C;border-radius:11px;
          padding:16px 18px;margin-bottom:10px; }
  .sig { color:#8B95A5;font-size:.8rem;font-family:"IBM Plex Mono",monospace;
         overflow-wrap:anywhere; }
  .unit { font-family:"IBM Plex Mono",monospace;font-size:.92rem; }
  .pill { font-family:"IBM Plex Mono",monospace;font-size:.75rem;padding:3px 9px;
          border-radius:5px;border:1px solid #2A313C;color:#8B95A5;white-space:nowrap; }
  .pill.bob { border-color:#F2545B;color:#F2545B;font-weight:600; }
  .stamp { font-family:"IBM Plex Mono",monospace;font-weight:600;letter-spacing:.06em;
           padding:5px 13px;border-radius:6px;border:1.5px solid currentColor;
           display:inline-block; }
  .gname { font-family:"IBM Plex Mono",monospace;font-size:.85rem;color:#8B95A5; }
  .gname.bad { color:#F2545B;font-weight:600; }
  .meter { height:7px;border-radius:4px;background:#12161D;overflow:hidden; }
  .meter span { display:block;height:100%;border-radius:4px;background:#42B77A; }
  .meter.bad span { background:#F2545B; }
  .foot { color:#6B7484;font-size:.82rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def pipeline():
    """Run ingest → split → triage → route once, and keep the Jev client."""
    config = Config(
        jev_api_key=None,
        jev_base_url="https://jev-agent.com/api/v1/systemone",
        jev_model="jev-latest",
        github_token=None,
        workdir=Path(tempfile.mkdtemp(prefix="trisolver-")),
        jev_mode="simulated",
    )
    result, client = triage_stage(config, "pricing-pr-482")
    return result, client


def meter(value: float, bad: bool) -> str:
    return (f'<div class="meter{" bad" if bad else ""}">'
            f'<span style="width:{round(value * 100)}%"></span></div>')


result, client = pipeline()

# ---------------------------------------------------------------- masthead
st.markdown('<div class="rule"></div>', unsafe_allow_html=True)
st.markdown("# TriSolver")
st.markdown('<p class="tagline">CI failure triage</p>', unsafe_allow_html=True)
st.markdown(
    "When CI goes red, most of the time goes on **sorting**, not fixing. TriSolver splits "
    "a failed build into one unit per failure, classifies each with Jev, sends only real "
    "regressions to IBM Bob — and blocks any patch that deletes a test."
)
st.caption(
    "ingest → split → triage (Jev) → route → dispatch (Bob) → verify (Jev)  ·  "
    "everything below is produced live by the real pipeline, in this browser session."
)

st.info(
    "**Jev is running in its offline simulator.** We could not obtain an API key, so the "
    "classifications below come from keyword heuristics, not the model. The integration is "
    "real code against Jev's documented `/v1/systemone` endpoint — the fallback is labelled "
    "everywhere it appears rather than hidden. The routing thresholds are tuned for real "
    "calibrated probabilities, which is why the flaky unit scores low here.",
    icon="ℹ️",
)

# ---------------------------------------------------------------- board
st.markdown("## One red build, five kinds of failure")
st.markdown(
    f"A single failed run, split into one unit per failing test or step. Below "
    f"**{MIN_CATEGORY_CONFIDENCE:.0%}** confidence nothing is automated and a person looks."
)

header = st.columns([1.4, 0.7, 0.8, 0.9, 1.3, 3.4])
for col, label in zip(header, ["CATEGORY", "CONF.", "FROM PR", "SEVERITY", "ACTION", "FAILURE"]):
    col.markdown(f'<span class="foot mono">{label}</span>', unsafe_allow_html=True)
st.divider()

for routed in result.routed:
    unit, triage = routed.unit, routed.triage
    cols = st.columns([1.4, 0.7, 0.8, 0.9, 1.3, 3.4])
    colour = CATEGORY_COLOUR.get(triage.category.value, "#8B95A5")
    cols[0].markdown(
        f'<span class="unit" style="color:{colour}">{triage.category.value}</span>',
        unsafe_allow_html=True)
    cols[1].markdown(f'<span class="unit">{triage.category_confidence:.0%}</span>',
                     unsafe_allow_html=True)
    cols[2].markdown(f'<span class="unit">{triage.caused_by_pr:.0%}</span>',
                     unsafe_allow_html=True)
    cols[3].markdown(f'<span class="unit">{triage.severity_label}</span>',
                     unsafe_allow_html=True)
    is_bob = routed.action is Action.DISPATCH_TO_BOB
    cols[4].markdown(
        f'<span class="pill{" bob" if is_bob else ""}">{ACTION_LABEL[routed.action]}</span>',
        unsafe_allow_html=True)
    below = triage.category_confidence < MIN_CATEGORY_CONFIDENCE
    extra = ('<br><span class="foot mono">below the confidence floor — not automated</span>'
             if below else "")
    cols[5].markdown(
        f'<span class="unit">{unit.name}</span><br>'
        f'<span class="sig">{unit.signature[:110]}</span>{extra}',
        unsafe_allow_html=True)

st.divider()
counts: dict[str, int] = {}
for routed in result.routed:
    counts[ACTION_LABEL[routed.action]] = counts.get(ACTION_LABEL[routed.action], 0) + 1
st.markdown(
    f"**{len(result.routed)} failure units** — "
    + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
)

with st.expander("What would TriSolver actually do about each one?"):
    for routed in result.routed:
        st.markdown(f"**{routed.unit.name}** — {routed.reason}")
        for effect in plan_effects(routed):
            st.markdown(f"- `{effect.kind}` → {effect.target} — {effect.detail}")

# ---------------------------------------------------------------- gates
st.markdown("## Checking the agent's work")
st.markdown(
    "The easiest way to make a failing test pass is to delete it. Every patch faces four "
    "Jev Noul gates **and** an independent, model-free read of the diff. Either signal "
    "blocks on its own."
)

regressions = result.regressions
if not regressions:
    st.warning("No regression reached the dispatch bar in this run.")
    st.stop()

target = regressions[0]
choice = st.radio("Proposed patch", list(PATCHES), horizontal=True, label_visibility="collapsed")
rel, blurb = PATCHES[choice]
st.caption(blurb)

patch_text = (ROOT / rel).read_text(encoding="utf-8")
proposal = PatchProposal(unit_id=target.unit.id, patch_text=patch_text, source="fixture")
verification = verify_patch(client, target, proposal, result.bundle)

left, right = st.columns([1.05, 1])

with left:
    colour = VERDICT_COLOUR[verification.decision]
    st.markdown(
        f'<span class="stamp" style="color:{colour}">{verification.decision.value.upper()}</span>'
        f'&nbsp;&nbsp;<span class="unit" style="color:#8B95A5">{target.unit.name}</span>',
        unsafe_allow_html=True)
    st.markdown("")

    thresholds = {
        "addresses_failure": lambda v: v < MIN_ADDRESSES_FAILURE,
        "touches_unrelated_files": lambda v: v > MAX_UNRELATED_FILES,
        "weakens_tests": lambda v: v > MAX_WEAKENS_TESTS,
        "follows_project_docs": lambda v: v < 0.40,
    }
    for name, value in verification.gates.items():
        bad = thresholds.get(name, lambda v: False)(value)
        gcols = st.columns([2.1, 0.6, 2.6])
        gcols[0].markdown(f'<span class="gname{" bad" if bad else ""}">{name}</span>',
                          unsafe_allow_html=True)
        gcols[1].markdown(f'<span class="unit">{value:.2f}</span>', unsafe_allow_html=True)
        gcols[2].markdown(meter(value, bad), unsafe_allow_html=True)

    st.markdown("")
    st.markdown(f'<span class="foot">static check &nbsp;·&nbsp; '
                f'{verification.static.summary()}</span>', unsafe_allow_html=True)
    st.markdown(f'<span class="foot">files touched &nbsp;·&nbsp; '
                f'{", ".join(verification.static.touched_files) or "none"}</span>',
                unsafe_allow_html=True)
    st.markdown("")
    for line in verification.reasons:
        st.markdown(f"> {line}")

with right:
    st.markdown("**The patch**")
    st.code(patch_text, language="diff")

# ---------------------------------------------------------------- bob
st.markdown("## What IBM Bob actually did")
st.markdown(
    "Bob ran headless in agent mode against a generated brief holding the failure, the "
    "implicated files, the PR diff, and the repository's own `CONTRIBUTING.md` — which "
    "states money is integer cents and rounding happens once, at the end."
)
bcols = st.columns(4)
for col, (label, value) in zip(bcols, [
        ("task", "3ed0eeca…"), ("duration", "149 s"),
        ("tool calls", "13"), ("cost", "$0.40")]):
    col.metric(label, value)
st.markdown(
    "One line: `return int(total)` → `return round(total)`. Truncating instead of rounding "
    "lost a cent on every discounted order. The test suite passes and the patch cleared "
    "all four gates."
)

# ---------------------------------------------------------------- run it
st.markdown("## TriSolver is a command-line tool")
st.markdown(
    "There is no server and nothing to log into — this page is a web front end over the "
    "same library. You point the CLI at one failed CI run and it prints the board above."
)
st.code(
    "git clone https://github.com/usernotfound-101/TriSolver && cd TriSolver\n"
    "uv sync\n\n"
    "# instant, offline, no key or token needed\n"
    "uv run trisolver demo --scenario cheat\n"
    "uv run trisolver triage pricing-pr-482\n\n"
    "# against a real run (needs GITHUB_TOKEN)\n"
    "uv run trisolver triage 'https://github.com/<owner>/<repo>/actions/runs/<id>' --live",
    language="bash",
)

good, caveat = st.columns(2)
with good:
    st.markdown("**Validated against**")
    st.markdown(
        "- Real failed runs in `psf/requests`, `pydantic/pydantic`, `urllib3/urllib3`, `encode/httpx`\n"
        "- A purpose-built sandbox on a self-hosted runner: five categories in one build\n"
        "- Bob's real patch cleared every gate; the suite passes\n"
        "- 75 tests"
    )
with caveat:
    st.markdown("**Stated plainly**")
    st.markdown(
        "- Jev runs in a labelled offline simulator — no API key yet\n"
        "- Subagent fan-out is built but undemonstrated: this run had one regression\n"
        "- No watsonx.ai or watsonx Orchestrate"
    )

st.divider()
st.markdown(
    '<span class="foot">TriSolver — Jev for judgement, deterministic code for policy, '
    'IBM Bob for the fix. Source at '
    '<a href="https://github.com/usernotfound-101/TriSolver">github.com/usernotfound-101/TriSolver</a>.'
    '</span>',
    unsafe_allow_html=True,
)
