"""TriSolver command line.

    trisolver fixtures                     list the recorded runs
    trisolver triage  <target>             split a red run and classify each failure
    trisolver dispatch <target>            triage, then write a Bob brief per regression
    trisolver verify  <target> --inbox     verify whatever patches Bob left behind
    trisolver run     <target>             the whole pipeline in one go
    trisolver demo                         the scripted end-to-end story, incl. the cheat

<target> is a fixture name, a fixture directory, or a GitHub Actions run URL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, report
from .bob.brief import write_brief
from .config import Config
from .ingest.base import list_fixtures
from .jev import JevClient, JevError
from .models import Action, Decision, PatchProposal, to_jsonable
from .pipeline import PipelineResult, fix_stage, triage_stage
from .route.actions import plan_effects
from .verify import verify_patch

DEMO_SCENARIOS = {
    "honest": "Bob fixes the rounding bug in the source. Every gate passes.",
    "cheat": "Bob deletes the failing test. The anti-cheat gate blocks it.",
    "skip": "Bob adds @pytest.mark.skip. Also blocked, by a different signal.",
    "sprawl": "Bob fixes it but reformats half the repo on the way. Held for a human.",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except (JevError, FileNotFoundError, ValueError, RuntimeError) as exc:
        print(report.c(f"error: {exc}", "red"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


# --------------------------------------------------------------------------
# commands


def cmd_fixtures(args: argparse.Namespace) -> int:
    names = list_fixtures()
    if not names:
        print("No fixtures found under fixtures/runs/.")
        return 1
    print(report.c("\n  recorded runs\n", "bold"))
    for name in names:
        print(f"    {name}")
    print(f"\n  try: trisolver run {names[0]}\n")
    return 0


def cmd_triage(args: argparse.Namespace) -> int:
    config = _config(args)
    result, client = triage_stage(config, args.target, live=args.live)
    client.close()
    if args.json:
        print(json.dumps(to_jsonable(result.routed), indent=2))
        return 0
    print(report.render_board(result.routed, result.jev_mode))
    if args.effects:
        _print_effects(result)
    _maybe_write_report(config, args, result)
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    config = _config(args)
    result, client = triage_stage(config, args.target, live=args.live)
    client.close()
    print(report.render_board(result.routed, result.jev_mode))

    regressions = result.regressions
    if not regressions:
        print(report.c("  nothing reached the bar for an agent. No briefs written.\n", "dim"))
        return 0

    config.briefs_dir.mkdir(parents=True, exist_ok=True)
    config.patches_dir.mkdir(parents=True, exist_ok=True)
    print(report.c(f"  wrote {len(regressions)} brief(s) for Bob:\n", "bold"))
    for routed in regressions:
        path = write_brief(routed, result.bundle, config.briefs_dir, config.patches_dir)
        print(f"    {path}")
    print(
        "\n  Next: open these in Bob, Agent mode, one subagent per brief.\n"
        f"  Each writes its unified diff to {config.patches_dir}/<unit-id>.patch, then run:\n"
        f"    trisolver verify {args.target} --inbox\n"
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    config = _config(args)
    result, client = triage_stage(config, args.target, live=args.live)

    targets = result.regressions
    if args.unit:
        targets = [r for r in result.routed if r.unit.id == args.unit or r.unit.name == args.unit]
        if not targets:
            client.close()
            raise ValueError(f"no failure unit matching {args.unit!r} in this run")

    verified = 0
    for routed in targets:
        patch_path = Path(args.patch) if args.patch else config.patches_dir / f"{routed.unit.id}.patch"
        if not patch_path.is_file():
            print(report.c(f"  pending: no patch at {patch_path}", "dim"))
            result.pending.append(routed.unit.id)
            continue
        proposal = PatchProposal(
            unit_id=routed.unit.id,
            patch_text=patch_path.read_text(encoding="utf-8"),
            source="inbox",
            notes=str(patch_path),
        )
        verification = verify_patch(client, routed, proposal, result.bundle)
        result.proposals[routed.unit.id] = proposal
        result.verifications[routed.unit.id] = verification
        print(report.render_verification(routed, verification))
        verified += 1

    client.close()
    print()
    _print_verdict_summary(result)
    _maybe_write_report(config, args, result)
    return 0 if _no_blocks(result) else 2


def cmd_run(args: argparse.Namespace) -> int:
    config = _config(args)
    result, client = triage_stage(config, args.target, live=args.live)
    print(report.render_board(result.routed, result.jev_mode))
    if args.effects:
        _print_effects(result)

    fixture_map = _load_fixture_map(args)
    result = fix_stage(
        config,
        result,
        client,
        bob_mode=args.bob,
        bob_cmd=args.bob_cmd,
        fixture_map=fixture_map,
        parallel=args.parallel,
    )
    client.close()

    if result.regressions:
        print(report.c("  BOB'S PATCHES, VERIFIED BY JEV", "bold"))
        for routed in result.regressions:
            verification = result.verifications.get(routed.unit.id)
            if verification is None:
                print(report.c(f"\n  PENDING       {routed.unit.name}", "dim"))
                print(report.c(f"    brief written; waiting for {config.patches_dir}/{routed.unit.id}.patch", "dim"))
                continue
            print(report.render_verification(routed, verification))
        print()
        _print_verdict_summary(result)

    _maybe_write_report(config, args, result)
    return 0 if _no_blocks(result) else 2


def cmd_demo(args: argparse.Namespace) -> int:
    scenario = args.scenario
    print(report.c(f"\n  DEMO: {scenario} -- {DEMO_SCENARIOS[scenario]}", "bold"))
    demo_args = argparse.Namespace(
        target=args.target,
        live=False,
        jev=args.jev,
        workdir=args.workdir,
        bob="fixture",
        bob_cmd=None,
        parallel=1,
        fixture_map=None,
        scenario=scenario,
        effects=True,
        report=args.report,
        json=False,
    )
    return cmd_run(demo_args)


# --------------------------------------------------------------------------
# helpers


def _config(args: argparse.Namespace) -> Config:
    return Config.load(workdir=Path(args.workdir), jev_mode=args.jev)


def _print_effects(result: PipelineResult) -> None:
    print(report.c("  WHAT TRISOLVER WOULD DO", "bold"))
    for routed in result.routed:
        print(report.c(f"\n    {routed.unit.name}", "dim"))
        for effect in plan_effects(routed):
            print(f"      - {effect.render()}")
    print()


def _print_verdict_summary(result: PipelineResult) -> None:
    counts = {d: 0 for d in Decision}
    for verification in result.verifications.values():
        counts[verification.decision] += 1
    parts = [f"{counts[d]} {d.value}" for d in Decision if counts[d]]
    if result.pending:
        parts.append(f"{len(result.pending)} pending")
    if not parts:
        return
    colour = "red" if counts[Decision.BLOCK] else ("yellow" if counts[Decision.HUMAN_REVIEW] else "green")
    print(report.c(f"  patch verdicts: {', '.join(parts)}\n", colour, "bold"))


def _no_blocks(result: PipelineResult) -> bool:
    return not any(v.decision is Decision.BLOCK for v in result.verifications.values())


def _maybe_write_report(config: Config, args: argparse.Namespace, result: PipelineResult) -> None:
    if not getattr(args, "report", False):
        return
    path = config.reports_dir / f"{result.bundle.name}.md"
    report.write_report(
        path,
        report.render_markdown(result.bundle.name, result.routed, result.verifications, result.jev_mode),
    )
    print(report.c(f"  report: {path}\n", "dim"))


def _load_fixture_map(args: argparse.Namespace) -> dict[str, str]:
    """For fixture mode, map each failing test to the patch Bob 'returned'."""
    if getattr(args, "fixture_map", None):
        return json.loads(Path(args.fixture_map).read_text(encoding="utf-8"))
    scenario = getattr(args, "scenario", None)
    if not scenario:
        return {}
    root = Path(__file__).resolve().parents[2] / "fixtures" / "patches" / "scenarios.json"
    if not root.is_file():
        return {}
    return json.loads(root.read_text(encoding="utf-8")).get(scenario, {})


# --------------------------------------------------------------------------
# parser


def _add_common(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--jev", choices=("auto", "live", "simulated"), default="auto",
                     help="auto uses JEV_API_KEY if present, else simulates (default: auto)")
    sub.add_argument("--workdir", default="work", help="where briefs, patches and reports live")
    sub.add_argument("--live", action="store_true", help="pull the run from the GitHub API")
    sub.add_argument("--report", action="store_true", help="also write a markdown report")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trisolver",
        description="CI failure triage: sort the red build, fix only what is real, and check the fix.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"trisolver {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    p = subparsers.add_parser("fixtures", help="list recorded runs")
    p.set_defaults(handler=cmd_fixtures)

    p = subparsers.add_parser("triage", help="split a red run into units and classify each")
    p.add_argument("target", help="fixture name, fixture dir, or Actions run URL")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--effects", action="store_true", help="show the actions that would be taken")
    _add_common(p)
    p.set_defaults(handler=cmd_triage)

    p = subparsers.add_parser("dispatch", help="write one Bob brief per high-confidence regression")
    p.add_argument("target")
    _add_common(p)
    p.set_defaults(handler=cmd_dispatch)

    p = subparsers.add_parser("verify", help="run the gates over patches Bob produced")
    p.add_argument("target")
    p.add_argument("--inbox", action="store_true", help="verify every patch in the patches dir")
    p.add_argument("--patch", help="verify this one patch file")
    p.add_argument("--unit", help="restrict to one failure unit id or name")
    _add_common(p)
    p.set_defaults(handler=cmd_verify)

    p = subparsers.add_parser("run", help="ingest, triage, route, dispatch and verify")
    p.add_argument("target")
    p.add_argument("--bob", choices=("inbox", "cli", "fixture"), default="inbox",
                   help="how to obtain Bob's patches (default: inbox)")
    p.add_argument("--bob-cmd", help="command for --bob cli; {brief} {patch} {unit} are substituted")
    p.add_argument("--fixture-map", help="JSON mapping of unit name -> patch file, for --bob fixture")
    p.add_argument("--parallel", type=int, default=4, help="max concurrent Bob subagents")
    p.add_argument("--effects", action="store_true")
    p.add_argument("--json", action="store_true")
    _add_common(p)
    p.set_defaults(handler=cmd_run, scenario=None)

    p = subparsers.add_parser("demo", help="scripted end-to-end run using recorded patches")
    p.add_argument("--scenario", choices=tuple(DEMO_SCENARIOS), default="cheat",
                   help="which patch Bob 'returns' (default: cheat)")
    p.add_argument("--target", default="pricing-pr-482", help="fixture to demo against")
    _add_common(p)
    p.set_defaults(handler=cmd_demo)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
