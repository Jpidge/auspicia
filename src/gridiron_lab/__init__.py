"""Gridiron Lab: fantasy-football analytics for the Fantasy Football Coalition."""

from gridiron_lab.scoring import (
    SCORING_VERSION,
    LeagueScoring,
    ScoreResult,
    ScoringError,
    defense_points_allowed,
    require_scoring_hash,
    score_defense,
    score_offense,
    score_offensive_frame,
)

__all__ = [
    "SCORING_VERSION",
    "LeagueScoring",
    "ScoreResult",
    "ScoringError",
    "defense_points_allowed",
    "require_scoring_hash",
    "score_defense",
    "score_offense",
    "score_offensive_frame",
]

__version__ = "0.4.0"
