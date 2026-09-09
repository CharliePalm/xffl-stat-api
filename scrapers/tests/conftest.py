"""Shared fixtures for scraper tests.

Every scraper module instantiates `PlayerService(SessionLocal())` as a
class attribute at import time, which pulls in `app.core.config.settings`
transitively through `shared.service.engine`. That settings object
requires `API_KEY`/`DB_PATH` env vars with no defaults, so they must be
set before anything under `scrapers/` or `shared/service/` is imported —
hence doing it here, at conftest module scope, before pytest imports any
test module that imports a scraper.

None of these tests touch that database for real: each one monkeypatches
the scraper's `player_service` lookup method directly (`get_by_name`,
`get_by_full_name`, or `search`, depending on the scraper) to hand back
`Player` objects built in memory, so there's no need to seed a real
database — only to survive class-body import.
"""

import os

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("DB_PATH", "/tmp/xffl-scraper-tests.db")

from pathlib import Path

import pytest

from shared.model import Game, NFLPosition, NFLTeam, Player
from shared.utils import clean_name

TEST_HTML = Path(__file__).resolve().parent.parent / "test_html"


class FakePlayers:
    """Deterministic, in-memory stand-in for player lookups.

    Scraper code asks for a player by (full) name + team, or in tank01's
    case by a source-specific id. This hands back a `Player` built on the
    fly, remembering it so the same lookup always returns the same `id` —
    which matters because a scraper accumulates one player's stats across
    several calls (e.g. rushing then receiving) keyed by `player.id`.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Player] = {}
        self._next_id = 1

    def _make(
        self, first: str, last: str, team: NFLTeam | None, position: NFLPosition
    ) -> Player:
        player = Player(
            id=self._next_id,
            first_name=first,
            last_name=last,
            team=team,
            active=True,
            position=position,
            first_name_norm=clean_name(first),
            last_name_norm=clean_name(last),
        )
        self._next_id += 1
        return player

    def by_name(
        self,
        full_name: str,
        team: NFLTeam | str | None,
        position: NFLPosition = NFLPosition.WR,
    ) -> Player:
        if team is None:
            team_obj = None
        elif isinstance(team, NFLTeam):
            team_obj = team
        else:
            team_obj = NFLTeam.from_abbreviation(str(team))
        key = (clean_name(full_name), team_obj.abbreviation if team_obj else "")
        if key not in self._by_key:
            first, *rest = full_name.split(" ")
            last = " ".join(rest) or first
            self._by_key[key] = self._make(first, last, team_obj, position)
        return self._by_key[key]

    def by_tank_id(
        self, tank_id: str, position: NFLPosition = NFLPosition.WR
    ) -> Player:
        key = ("__tank__", tank_id)
        if key not in self._by_key:
            player = self._make("Player", tank_id, None, position)
            self._by_key[key] = player.model_copy(update={"tank_id": tank_id})
        return self._by_key[key]

    def name(self, player_id: int) -> str:
        """Display label for a `player_id` this registry minted."""
        for player in self._by_key.values():
            if player.id == player_id:
                team = f" ({player.team.abbreviation})" if player.team else ""
                return f"{player.first_name} {player.last_name}{team}"
        return str(player_id)


@pytest.fixture
def fake_players() -> FakePlayers:
    return FakePlayers()


@pytest.fixture
def sf_vs_lac() -> Game:
    """The 49ers @ Chargers preseason game the cbs/espn/pff/tank01 fixtures
    all describe (final: SF 41, LAC 17)."""
    return Game(
        week=-2,
        home=NFLTeam.LOS_ANGELES_CHARGERS,
        away=NFLTeam.SAN_FRANCISCO_49ERS,
        cbs_link="https://www.cbssports.com/nfl/gametracker/boxscore/NFL_20260820_SF@LAC/",
        espn_link="https://www.espn.com/nfl/boxscore/_/gameId/401773186",
        date_time="2026-08-20 20:00:00",
        pff_id=1,
        neutral_site=False,
        season="2026",
        in_progress=False,
    )
