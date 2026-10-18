"""Shared fixtures for the scoring tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from gridiron_lab.config import AppConfig, load_config
from gridiron_lab.scoring import LeagueScoring

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_config(REPO_ROOT / "gridiron_lab.toml")


@pytest.fixture(scope="session")
def rules(config: AppConfig) -> LeagueScoring:
    return config.scoring
