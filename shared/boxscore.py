"""Source-agnostic accumulation and fantasy scoring of a single game's box score.

Scrapers are responsible only for turning their site's markup into `Stat` values;
everything downstream of that — merging a player's lines across stat categories,
inferring positions, synthesizing D/ST entries, and applying the scoring rules —
lives here so every source produces identical `PlayerWeekData`.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from shared.model import NFLPosition, NFLTeam, Player, PlayerWeekData, ScrapedPageInfo
from shared.calculation_utils import (
    DefensiveStatLine,
    OffensiveStatLine,
    calculate_defense_score,
    calculate_offensive_score,
)


class Stat(StrEnum):
    """Canonical stat names. Each scraper maps its own column headers onto these."""

    passing_yards = "passing_yards"
    passing_tds = "passing_tds"
    interceptions_thrown = "interceptions_thrown"
    rushing_attempts = "rushing_attempts"
    rushing_yards = "rushing_yards"
    rushing_tds = "rushing_tds"
    receptions = "receptions"
    receiving_yards = "receiving_yards"
    receiving_tds = "receiving_tds"
    fumbles_lost = "fumbles_lost"
    two_pt_conversions = "two_pt_conversions"
    # kickers are scored off the box score's own points column: standard fantasy
    # awards distance bonuses, which aggregated FG columns cannot reconstruct
    kicking_points = "kicking_points"
    # team defense
    sacks = "sacks"
    interceptions = "interceptions"
    fumbles_recovered = "fumbles_recovered"
    defensive_tds = "defensive_tds"
    safeties = "safeties"
    blocked_kicks = "blocked_kicks"

    @property
    def is_offensive(self) -> bool:
        return self in _OFFENSIVE_STATS


_OFFENSIVE_STATS = frozenset(
    {
        Stat.passing_yards,
        Stat.passing_tds,
        Stat.interceptions_thrown,
        Stat.rushing_attempts,
        Stat.rushing_yards,
        Stat.rushing_tds,
        Stat.receptions,
        Stat.receiving_yards,
        Stat.receiving_tds,
        Stat.fumbles_lost,
        Stat.two_pt_conversions,
    }
)

TStats = dict[Stat, float]


class StatLine(BaseModel):
    player: Player
    stats: TStats


class BoxscoreBuilder:
    """Accumulates one game's stats, then emits scored `PlayerWeekData`."""

    def __init__(self, week: int):
        self.week = week
        self._players: dict[int, StatLine] = {}
        self._positions: dict[int, NFLPosition] = {}
        self._defenses: dict[NFLTeam, TStats] = {}
        self._final_score: dict[NFLTeam, int] = {}

    def add_player(
        self,
        player: Player,
        stats: Mapping[Stat, float],
    ) -> None:
        """Merge one stat-category row into a player's line. Safe to call repeatedly."""
        line = self._players.setdefault(
            player.id, StatLine(**{"player": player, "stats": {}})
        )
        for stat, value in stats.items():
            line.stats[stat] = line.stats.get(stat, 0.0) + value

    def add_team_defense(self, team: NFLTeam, stats: Mapping[Stat, float]) -> None:
        """Accumulate D/ST stats — per defender (CBS) or per team totals row (ESPN)."""
        line = self._defenses.setdefault(team, {})
        for stat, value in stats.items():
            line[stat] = line.get(stat, 0.0) + value

    def set_final_score(self, team: NFLTeam, points: int) -> None:
        self._final_score[team] = points

    def build(self, home_team: NFLTeam, away_team: NFLTeam) -> ScrapedPageInfo:
        return ScrapedPageInfo(
            home_team=home_team,
            away_team=away_team,
            player_week_data=self._score_players() + self._score_defenses(),
        )

    def _score_players(self) -> list[PlayerWeekData]:
        scored: list[PlayerWeekData] = []
        for id, stat_line in self._players.items():
            stats = OffensiveStatLine()
            if stat_line.player.position is NFLPosition.K:
                print(stat_line)
                points = stat_line.stats.get(Stat.kicking_points, 0.0)
            else:
                stats = OffensiveStatLine(
                    **{
                        stat.value: value
                        for stat, value in stat_line.stats.items()
                        if stat.is_offensive
                    }  # type: ignore
                )
                points = calculate_offensive_score(stats)
            scored.append(
                PlayerWeekData(
                    player_id=id,
                    week=self.week,
                    points=points,
                    **stats.__dict__,
                )
            )
        return scored

    def _score_defenses(self) -> list[PlayerWeekData]:
        defenses: list[PlayerWeekData] = []
        for team, line in self._defenses.items():
            # a defense allows whatever its opponent scored
            allowed = next(
                (pts for other, pts in self._final_score.items() if other is not team),
                0,
            )
            stat_line = DefensiveStatLine(
                points_allowed=allowed,
                **{stat.value: int(value) for stat, value in line.items()},
            )
            defenses.append(
                PlayerWeekData(
                    player_id=team.id,
                    week=self.week,
                    points=calculate_defense_score(stat_line),
                    **stat_line.__dict__,
                )
            )
        return defenses
