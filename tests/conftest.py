from pathlib import Path

import pytest

from trisolver.config import Config
from trisolver.ingest import load_run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle():
    return load_run("pricing-pr-482")


@pytest.fixture
def config(tmp_path):
    return Config(
        jev_api_key=None,
        jev_base_url="https://example.invalid/v1/systemone",
        jev_model="jev-latest",
        github_token=None,
        workdir=tmp_path / "work",
        jev_mode="simulated",
    )


@pytest.fixture
def patch_text():
    def _read(name: str) -> str:
        return (ROOT / "fixtures" / "patches" / name).read_text(encoding="utf-8")

    return _read
