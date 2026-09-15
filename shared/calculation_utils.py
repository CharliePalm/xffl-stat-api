from pydantic import BaseModel
from typing import Any

from shared.model import DefensiveStatLine, OffensiveStatLine


def _get(stat_line: Any, stat: str) -> Any:
    if isinstance(stat_line, BaseModel):
        return getattr(stat_line, stat, 0) or 0
    # assume mapping-like
    return stat_line.get(stat, 0)


# ---------------------------------------------------------------------------
# OFFENSE
# ---------------------------------------------------------------------------
# Flat per-occurrence point values.
OFFENSIVE_MULTIPLIERS: dict[str, float] = {
    "passing_tds": 4.0,  # 4 per TD passing
    "rushing_tds": 6.0,  # 6 per TD rushing
    "receiving_tds": 6.0,  # 6 per TD receiving
    "offensive_fumble_recovery_tds": 6.0,  # 6 per offensive fumble recovery TD
    "receptions": 0.0,
    "fumbles_lost": -1.0,  # -1 per fumble lost
    "interceptions_thrown": -1.0,  # -1 per INT thrown
    "two_pt_conversions": 2.0,  # 2 per rushing/receiving/passing 2-pt conversion
    "extra_points_made": 1.0,  # 1 per PAT
}

# (stat name, yards per point) — whole-number floor division, NOT a
# continuous multiplier: e.g. 19 rushing yards is still only 1 point.
OFFENSIVE_YARDAGE_RULES: list[tuple[str, int]] = [
    ("rushing_yards", 10),
    ("receiving_yards", 10),
    ("passing_yards", 20),
]

# Yardage milestone bonuses, on top of the per-10/20-yard scoring above.
# (inclusive lower bound, inclusive upper bound, bonus). Ranges are
# mutually exclusive — 200+ rushing/receiving replaces the 100-199 bonus,
# it doesn't stack with it. Assumes rushing and receiving yards are each
# milestone-eligible independently (not combined into one "yards from
# scrimmage" total) — flag if that's not how your league scores it.
RUSH_RECEIVING_MILESTONE_TIERS: list[tuple[float, float, float]] = [
    (100, 199, 2.0),
    (200, float("inf"), 5.0),
]
PASSING_MILESTONE_TIERS: list[tuple[float, float, float]] = [
    (300, 399, 2.0),
    (400, float("inf"), 5.0),
]

# Field goal scoring: flat +3 per FG made (already in OFFENSIVE_MULTIPLIERS'
# spirit, handled separately here since it stacks with a distance bonus),
# plus a distance bonus so a 38-52 yarder is worth 5 total and a 53+ yarder
# is worth 8 total, plus a flat +2 bonus for going 3-for-3+ with no misses.
FIELD_GOAL_BASE_POINTS = 3.0
FIELD_GOAL_DISTANCE_TIERS: list[tuple[float, float, float]] = [
    (38, 52, 2.0),
    (53, float("inf"), 5.0),
]
PERFECT_FG_GAME_MIN_MAKES = 3
PERFECT_FG_GAME_BONUS = 2.0

DEFENSIVE_BASE_SCORE = 15


def _milestone_bonus(
    yards: float, bonus_tiers: list[tuple[float, float, float]]
) -> float:
    for lo, hi, bonus in bonus_tiers:
        if lo <= yards <= hi:
            return bonus
    return 0.0


def _field_goal_score(stat_line: OffensiveStatLine) -> float:
    """Scores made field goals individually by distance, plus the perfect-
    game bonus. The model stores the made distances as `field_goals_made`
    and the missed count as `field_goals_missed`."""
    made_distances = _get(stat_line, "field_goals_made") or []
    score = len(made_distances) * FIELD_GOAL_BASE_POINTS

    score += sum(
        _milestone_bonus(distance, FIELD_GOAL_DISTANCE_TIERS)
        for distance in made_distances
    )

    if (
        len(made_distances) >= PERFECT_FG_GAME_MIN_MAKES
        and stat_line.field_goals_missed == 0
    ):
        score += PERFECT_FG_GAME_BONUS

    return score


def calculate_offensive_score(stat_line: OffensiveStatLine) -> float:
    """Calculates Offensive Player fantasy points for standard non-PPR scoring using typed dictionary input."""

    score = sum(
        _get(stat_line, stat) * multiplier
        for stat, multiplier in OFFENSIVE_MULTIPLIERS.items()
    )
    score += sum(
        (_get(stat_line, stat) // yards_per_point)
        for stat, yards_per_point in OFFENSIVE_YARDAGE_RULES
    )
    score += _milestone_bonus(
        _get(stat_line, "rushing_yards"), RUSH_RECEIVING_MILESTONE_TIERS
    )
    score += _milestone_bonus(
        _get(stat_line, "receiving_yards"), RUSH_RECEIVING_MILESTONE_TIERS
    )
    score += _milestone_bonus(_get(stat_line, "passing_yards"), PASSING_MILESTONE_TIERS)
    res = _field_goal_score(stat_line)
    score += res
    return round(float(score), 2)


# ---------------------------------------------------------------------------
# DEFENSE
# ---------------------------------------------------------------------------
DEFENSIVE_MULTIPLIERS: dict[str, float] = {
    "sacks": 1.0,  # 1 per sack
    "interceptions": 2.0,  # 2 per turnover
    "fumbles_recovered": 2.0,  # 2 per turnover
    "safeties": 2.0,  # 2 per safety
    "defensive_tds": 6.0,  # 6 per TD
    # "blocked_kicks": 2.0,
    "tds_allowed": -3.0,
}

YARDS_ALLOWED_MULTIPLIER = -1


def _score_yards_allowed(yards_allowed: int) -> float:
    return (yards_allowed // 100) * YARDS_ALLOWED_MULTIPLIER


def _score_shutout_bonus(stat_line: DefensiveStatLine) -> float:
    """6 points for a shutout (0 points allowed), else 3 points if no TDs
    were given up (e.g. opponent only scored via FG). Not both — a shutout
    already implies no TDs, so it just wins on its own."""
    if int(stat_line.points_allowed or 0) == 0:
        return 6.0
    return 0.0


def calculate_defense_score(stat_line: DefensiveStatLine) -> float:
    """Calculates Fantasy D/ST points for standard non-PPR scoring using typed dictionary input."""

    score = DEFENSIVE_BASE_SCORE + sum(
        _get(stat_line, stat) * multiplier
        for stat, multiplier in DEFENSIVE_MULTIPLIERS.items()
    )
    print(score)
    score += _score_yards_allowed(int(stat_line.yards_allowed or 0))
    print(score)
    score += _score_shutout_bonus(stat_line)
    print(score)
    return float(score)
