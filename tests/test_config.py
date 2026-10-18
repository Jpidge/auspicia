"""Config loading, the shipped league table, and secret rejection."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gridiron_lab.config import ConfigError, load_config
from gridiron_lab.scoring import SCORING_VERSION, LeagueScoring

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "gridiron_lab.toml"


def test_shipped_league_identity(rules: LeagueScoring, config) -> None:
    assert config.league.name == "Fantasy Football Coalition"
    assert config.league.yahoo_id == 893771
    assert config.league.teams == 12
    assert config.league.my_team == "Purple Reign"
    assert config.league.my_team_id == 10
    assert config.league.ppr is False
    assert config.league.fractional is True
    assert config.league.negative_scoring is True
    assert config.league.scoring_version == SCORING_VERSION
    assert rules.version == SCORING_VERSION
    assert config.lineup.qb == 1
    assert config.lineup.rb == 2
    assert config.lineup.wr == 2
    assert config.lineup.te == 1
    assert config.lineup.flex == 1
    assert config.lineup.k == 1
    assert config.lineup.dst == 1
    assert config.lineup.bench == 6
    assert config.lineup.ir == 1


def test_shipped_table_matches_the_league_rules(rules: LeagueScoring) -> None:
    assert rules.passing_yards_per_point == 25
    assert rules.rushing_yards_per_point == 10
    assert rules.receiving_yards_per_point == 10
    assert rules.return_yards_per_point == 25
    assert rules.field_goal_yards_per_point == 10
    assert rules.passing_touchdown == 6
    assert rules.interception == -2
    assert rules.passing_touchdown_40_plus == 1
    assert rules.rushing_touchdown == 6
    assert rules.rushing_touchdown_40_plus == 1
    assert rules.reception == 0
    assert rules.receiving_touchdown == 6
    assert rules.receiving_touchdown_40_plus == 1
    assert rules.return_touchdown == 6
    assert rules.two_point_conversion == 2
    assert rules.fumble_lost == -2
    assert rules.offensive_fumble_return_touchdown == 6
    assert rules.extra_point_made == 1
    assert rules.defense_sack == 1
    assert rules.defense_interception == 2
    assert rules.defense_fumble_recovery == 2
    assert rules.defense_touchdown == 6
    assert rules.defense_safety == 2
    assert rules.defense_blocked_kick == 2
    assert rules.defense_return_touchdown == 6
    assert rules.defense_extra_point_return == 2
    assert [(tier.max_points, tier.points) for tier in rules.points_allowed] == [
        (0, 10.0),
        (6, 7.0),
        (13, 4.0),
        (20, 1.0),
        (27, 0.0),
        (34, -1.0),
        (None, -4.0),
    ]


def test_load_logs_a_stage_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="gridiron_lab.config")
    loaded = load_config(CONFIG_PATH)
    assert "ffc_yahoo_v1" in caplog.text
    assert loaded.scoring.table_hash[:12] in caplog.text
    assert "Fantasy Football Coalition" in caplog.text


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Could not read"):
        load_config(tmp_path / "gridiron_lab.toml")


def test_bad_toml_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "gridiron_lab.toml"
    path.write_text("this = [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="parse") as caught:
        load_config(path)
    assert isinstance(caught.value.__cause__, Exception)
    assert type(caught.value.__cause__).__name__ == "TOMLDecodeError"


def test_secret_key_is_rejected_without_echoing_the_value(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "gridiron_lab.toml"
    path.write_text(
        CONFIG_PATH.read_text(encoding="utf-8") + '\nrefresh_token = "super-secret-value"\n',
        encoding="utf-8",
    )
    caplog.set_level(logging.ERROR, logger="gridiron_lab.config")
    with pytest.raises(ConfigError, match="secret") as caught:
        load_config(path)
    assert "super-secret-value" not in str(caught.value)
    assert "super-secret-value" not in caplog.text


def test_non_fractional_config_is_rejected(tmp_path: Path) -> None:
    path = _variant(tmp_path, "fractional = true", "fractional = false")
    with pytest.raises(ConfigError, match="fractional"):
        load_config(path)


def test_ppr_flag_must_match_reception_points(tmp_path: Path) -> None:
    path = _variant(tmp_path, "ppr = false", "ppr = true")
    with pytest.raises(ConfigError, match="reception"):
        load_config(path)


def test_negative_table_requires_negative_scoring(tmp_path: Path) -> None:
    path = _variant(tmp_path, "negative_scoring = true", "negative_scoring = false")
    with pytest.raises(ConfigError, match="zero floor"):
        load_config(path)


def test_unknown_event_key_is_rejected(tmp_path: Path) -> None:
    path = _variant(tmp_path, "defense_safety = 2\n", "defense_safety = 2\nbonus = 1\n")
    with pytest.raises(ConfigError, match="unexpected"):
        load_config(path)


def test_missing_event_key_is_rejected(tmp_path: Path) -> None:
    path = _variant(tmp_path, "defense_safety = 2\n", "")
    with pytest.raises(ConfigError, match="defense_safety"):
        load_config(path)


def _variant(tmp_path: Path, old: str, new: str) -> Path:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "gridiron_lab.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path
