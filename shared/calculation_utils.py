from pydantic import BaseModel
from typing import Any

from shared.model import DefensiveStatLine, OffensiveStatLine

# Per-unit fantasy point value of each stat. Edit these to retune scoring —
# e.g. set "receptions" to 1.0 for PPR, 0.5 for half-PPR.
OFFENSIVE_MULTIPLIERS: dict[str, float] = {
    "passing_yards": 0.04,
    "passing_tds": 4.0,
    "interceptions_thrown": -2.0,
    "rushing_yards": 0.1,
    "rushing_tds": 6.0,
    "receptions": 0.0,
    "receiving_yards": 0.1,
    "receiving_tds": 6.0,
    "fumbles_lost": -2.0,
    "two_pt_conversions": 2.0,
}

DEFENSIVE_MULTIPLIERS: dict[str, float] = {
    "sacks": 1.0,
    "interceptions": 2.0,
    "fumbles_recovered": 2.0,
    "safeties": 2.0,
    "defensive_tds": 6.0,
    "blocked_kicks": 2.0,
}

# Points-allowed tiers, as (upper bound inclusive, score) checked in ascending
# order. The last entry's bound is unused - it always matches whatever falls
# through the rest, i.e. "35+".
POINTS_ALLOWED_TIERS: list[tuple[int | float, float]] = [
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (17, 1.0),
    (27, 0.0),
    (34, -1.0),
    (float("inf"), -4.0),
]


def _score_points_allowed(points_allowed: int) -> float:
    for upper_bound, score in POINTS_ALLOWED_TIERS:
        if points_allowed <= upper_bound:
            return score
    return POINTS_ALLOWED_TIERS[-1][1]


def calculate_defense_score(stat_line: DefensiveStatLine) -> float:
    """Calculates Fantasy D/ST points for standard non-PPR scoring using typed dictionary input."""

    def _get(stat: str) -> Any:
        if isinstance(stat_line, BaseModel):
            return getattr(stat_line, stat, 0) or 0
        # assume mapping-like
        return stat_line.get(stat, 0)

    score = sum(
        _get(stat) * multiplier for stat, multiplier in DEFENSIVE_MULTIPLIERS.items()
    )
    score += _score_points_allowed(int(_get("points_allowed")))
    return float(score)


def calculate_offensive_score(stat_line: OffensiveStatLine) -> float:
    """Calculates Offensive Player fantasy points for standard non-PPR scoring using typed dictionary input."""

    def _get(stat: str) -> Any:
        if isinstance(stat_line, BaseModel):
            return getattr(stat_line, stat, 0) or 0
        return stat_line.get(stat, 0)

    score = sum(
        _get(stat) * multiplier for stat, multiplier in OFFENSIVE_MULTIPLIERS.items()
    )
    return round(float(score), 2)
