"""The whole pipeline, wired end to end.

    ingest -> split -> triage (Jev) -> route -> dispatch (Bob) -> verify (Jev)

Each stage is importable on its own; this module is just the wiring, so the
CLI stays thin and the stages stay testable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .bob import make_adapter
from .config import Config
from .ingest import RunBundle, load_run
from .jev import JevClient
from .models import Action, FailureUnit, PatchProposal, Routed, Triage, Verification
from .parse import split_failures
from .route import route_all
from .triage import triage_unit
from .verify import verify_patch


@dataclass(slots=True)
class PipelineResult:
    bundle: RunBundle
    units: list[FailureUnit]
    routed: list[Routed]
    proposals: dict[str, PatchProposal] = field(default_factory=dict)
    verifications: dict[str, Verification] = field(default_factory=dict)
    briefs: list[Path] = field(default_factory=list)
    jev_mode: str = "simulated"
    pending: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[Routed]:
        return [r for r in self.routed if r.action is Action.DISPATCH_TO_BOB]


def triage_stage(
    config: Config,
    target: str,
    *,
    live: bool = False,
    client: JevClient | None = None,
) -> tuple[PipelineResult, JevClient]:
    """Ingest, split, triage and route. The half that always runs."""
    bundle = load_run(target, live=live, token=config.github_token)
    units = split_failures(bundle.raw_log)

    owned = client is None
    client = client or JevClient(config)
    try:
        pairs: list[tuple[FailureUnit, Triage]] = [
            (unit, triage_unit(client, unit, bundle.diff)) for unit in units
        ]
    except Exception:
        if owned:
            client.close()
        raise

    routed = route_all(pairs)
    result = PipelineResult(
        bundle=bundle, units=units, routed=routed, jev_mode=client.mode
    )
    return result, client


def fix_stage(
    config: Config,
    result: PipelineResult,
    client: JevClient,
    *,
    bob_mode: str = "inbox",
    bob_cmd: str | None = None,
    fixture_map: dict[str, str] | None = None,
    parallel: int = 4,
) -> PipelineResult:
    """Dispatch every high-confidence regression to Bob, then verify what it returns.

    One subagent per regression: in ``cli`` mode they genuinely run in
    parallel, which is the whole reason the failure units are independent.
    """
    adapter = make_adapter(
        bob_mode,
        briefs_dir=config.briefs_dir,
        patches_dir=config.patches_dir,
        bob_cmd=bob_cmd,
        fixture_map=fixture_map,
    )
    config.patches_dir.mkdir(parents=True, exist_ok=True)

    targets = result.regressions
    if not targets:
        return result

    workers = max(1, min(parallel, len(targets)))
    if workers > 1 and bob_mode == "cli":
        with ThreadPoolExecutor(max_workers=workers) as pool:
            proposals = list(pool.map(lambda r: (r, adapter.propose(r, result.bundle)), targets))
    else:
        proposals = [(r, adapter.propose(r, result.bundle)) for r in targets]

    for routed, proposal in proposals:
        if proposal is None or not proposal.patch_text.strip():
            result.pending.append(routed.unit.id)
            continue
        result.proposals[routed.unit.id] = proposal
        result.verifications[routed.unit.id] = verify_patch(client, routed, proposal, result.bundle)

    briefs = getattr(adapter, "written_briefs", None)
    if briefs:
        result.briefs = list(briefs)
    else:
        result.briefs = sorted(config.briefs_dir.glob("*.md"))
    return result
