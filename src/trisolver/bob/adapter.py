"""Stage 5b: get a patch back from Bob.

Three ways in, all producing the same ``PatchProposal``:

``inbox``   (default) Write the brief, then read whatever appears at
            ``work/patches/<unit-id>.patch``. This is the honest path for a
            machine with no Bob CLI: you run Bob in Agent mode against the
            briefs, one subagent per regression, and it writes the patches.
``cli``     Shell out to a configurable command (``--bob-cmd``), so if a
            headless Bob is available it drops straight in.
``fixture`` Read a pre-recorded patch from ``fixtures/patches/``. Used by
            ``trisolver demo`` to show the verification gates deterministically,
            including the one where the agent cheats.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Protocol

from ..ingest import RunBundle
from ..models import PatchProposal, Routed
from .brief import write_brief

FIXTURE_PATCHES = Path(__file__).resolve().parents[3] / "fixtures" / "patches"


class BobAdapter(Protocol):
    name: str

    def propose(self, routed: Routed, bundle: RunBundle) -> PatchProposal | None:
        """Return a patch for this regression, or None if none is available yet."""


class InboxAdapter:
    """Write a brief; pick up a patch a human-driven Bob leaves behind."""

    name = "inbox"

    def __init__(self, briefs_dir: Path, patches_dir: Path) -> None:
        self.briefs_dir = briefs_dir
        self.patches_dir = patches_dir
        self.written_briefs: list[Path] = []

    def propose(self, routed: Routed, bundle: RunBundle) -> PatchProposal | None:
        self.written_briefs.append(write_brief(routed, bundle, self.briefs_dir, self.patches_dir))
        patch_path = self.patches_dir / f"{routed.unit.id}.patch"
        if not patch_path.is_file():
            return None
        return PatchProposal(
            unit_id=routed.unit.id,
            patch_text=patch_path.read_text(encoding="utf-8"),
            source="inbox",
            notes=f"read from {patch_path}",
        )


class CliAdapter:
    """Run a headless Bob. ``{brief}``, ``{patch}`` and ``{unit}`` are substituted."""

    name = "cli"

    def __init__(self, command: str, briefs_dir: Path, patches_dir: Path, timeout: int = 900) -> None:
        self.command = command
        self.briefs_dir = briefs_dir
        self.patches_dir = patches_dir
        self.timeout = timeout

    def propose(self, routed: Routed, bundle: RunBundle) -> PatchProposal | None:
        brief_path = write_brief(routed, bundle, self.briefs_dir, self.patches_dir)
        patch_path = self.patches_dir / f"{routed.unit.id}.patch"
        self.patches_dir.mkdir(parents=True, exist_ok=True)

        rendered = self.command.format(
            brief=str(brief_path), patch=str(patch_path), unit=routed.unit.id
        )
        try:
            result = subprocess.run(  # noqa: S603 - command is operator-supplied
                shlex.split(rendered),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PatchProposal(routed.unit.id, "", "bob-cli", notes=f"bob command failed: {exc}")

        if patch_path.is_file():
            text = patch_path.read_text(encoding="utf-8")
        else:
            text = result.stdout  # a Bob that prints the diff instead of writing it
            if text.strip():
                patch_path.write_text(text, encoding="utf-8")
        if not text.strip():
            return PatchProposal(
                routed.unit.id, "", "bob-cli",
                notes=f"bob exited {result.returncode} with no patch: {result.stderr[:200]}",
            )
        return PatchProposal(routed.unit.id, text, "bob-cli", notes=f"exit {result.returncode}")


class FixtureAdapter:
    """Serve pre-recorded patches, for a deterministic demo of the gates."""

    name = "fixture"

    def __init__(self, mapping: dict[str, str], briefs_dir: Path, patches_dir: Path) -> None:
        self.mapping = mapping
        self.briefs_dir = briefs_dir
        self.patches_dir = patches_dir

    def propose(self, routed: Routed, bundle: RunBundle) -> PatchProposal | None:
        write_brief(routed, bundle, self.briefs_dir, self.patches_dir)
        filename = self.mapping.get(routed.unit.id) or self.mapping.get(routed.unit.name)
        if not filename:
            # Fall back to matching on the test's short name, which is how the
            # fixture mapping is keyed when unit ids are hash-suffixed.
            tail = routed.unit.name.split("::")[-1]
            filename = self.mapping.get(tail)
        if not filename:
            return None
        path = FIXTURE_PATCHES / filename
        if not path.is_file():
            return None
        return PatchProposal(
            unit_id=routed.unit.id,
            patch_text=path.read_text(encoding="utf-8"),
            source="fixture",
            notes=f"recorded patch {filename}",
        )


def make_adapter(
    mode: str,
    *,
    briefs_dir: Path,
    patches_dir: Path,
    bob_cmd: str | None = None,
    fixture_map: dict[str, str] | None = None,
) -> BobAdapter:
    match mode:
        case "cli":
            if not bob_cmd:
                raise ValueError("--bob cli requires --bob-cmd, e.g. --bob-cmd 'bob run --task {brief}'")
            return CliAdapter(bob_cmd, briefs_dir, patches_dir)
        case "fixture":
            return FixtureAdapter(fixture_map or {}, briefs_dir, patches_dir)
        case "inbox":
            return InboxAdapter(briefs_dir, patches_dir)
    raise ValueError(f"unknown Bob mode {mode!r}; expected inbox, cli, or fixture")
