"""Runtime configuration, read from the environment with sane defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_JEV_URL = "https://jev-agent.com/api/v1/systemone"

# Routing thresholds. Deliberately explicit constants rather than magic numbers
# buried in the router: at a demo these are the dials people ask about.
MIN_CATEGORY_CONFIDENCE = 0.65  # below this, no automation, a human looks
MIN_DISPATCH_CONFIDENCE = 0.75  # below this, a regression is not handed to Bob
MIN_CAUSED_BY_PR = 0.60  # below this, the regression predates the PR

# Verification gates. A patch must clear all of these to be surfaced.
MAX_WEAKENS_TESTS = 0.30  # the anti-cheat gate, held tightest
MAX_UNRELATED_FILES = 0.50
MIN_ADDRESSES_FAILURE = 0.60


@dataclass(slots=True)
class Config:
    jev_api_key: str | None
    jev_base_url: str
    jev_model: str
    github_token: str | None
    workdir: Path
    jev_mode: str  # "auto" | "live" | "simulated"

    @classmethod
    def load(cls, workdir: Path | None = None, jev_mode: str = "auto") -> "Config":
        _load_dotenv(Path(".env"))
        return cls(
            jev_api_key=os.environ.get("JEV_API_KEY") or None,
            jev_base_url=os.environ.get("JEV_BASE_URL", DEFAULT_JEV_URL),
            jev_model=os.environ.get("JEV_MODEL", "jev-latest"),
            github_token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None,
            workdir=workdir or Path("work"),
            jev_mode=jev_mode,
        )

    @property
    def briefs_dir(self) -> Path:
        return self.workdir / "regressions"

    @property
    def patches_dir(self) -> Path:
        return self.workdir / "patches"

    @property
    def reports_dir(self) -> Path:
        return self.workdir / "reports"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
