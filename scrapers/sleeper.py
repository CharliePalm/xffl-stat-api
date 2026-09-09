import json
from typing import Any
from shared.model import Game, NFLTeam, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.next_flight import find_objects, reconstruct_flight_text
from shared.service.engine import SessionLocal
from shared.service.game_service import GameService
from shared.service.player_service import PlayerService

# player.position -> the raw `stats` keys we take for that stat category
OFFENSE_COLUMNS: dict[Stat, str] = {
    Stat.passing_yards: "pass_yd",
    Stat.passing_tds: "pass_td",
    Stat.interceptions_thrown: "pass_int",
    Stat.rushing_attempts: "rush_att",
    Stat.rushing_yards: "rush_yd",
    Stat.rushing_tds: "rush_td",
    Stat.receptions: "rec",
    Stat.receiving_yards: "rec_yd",
    Stat.receiving_tds: "rec_td",
    Stat.fumbles_lost: "fum_lost",
}
TWO_PT_COLUMNS = ("pass_2pt", "rush_2pt", "rec_2pt")
OFFENSE_POSITIONS = {"QB", "RB", "WR", "TE"}
DEFENSE_COLUMNS: dict[Stat, str | list[str]] = {
    Stat.sacks: "sack",
    Stat.interceptions: "int",
    Stat.fumbles_recovered: "fum_rec",
    Stat.defensive_tds: ["def_st_td", "def_td"],
}

session = SessionLocal()


class SleeperScraper(Scraper[str]):
    file_name = "9ers_chargers_sleeper.html"
    player_service = PlayerService(session)
    game_service = GameService(session)

    @staticmethod
    def get_url(game: Game):
        away_abbrev = game.away.abbreviation.lower()
        away_team = game.away.team_name.lower()
        home_abbrev = game.home.abbreviation.lower()
        home_team = game.home.team_name.lower()
        date = game.date_time.split(" ")[0]
        return f"https://sleeper.com/nfl/scores/{away_abbrev}-{away_team}-{home_abbrev}-{home_team}-{date}"

    def parse_html(self, html: str) -> str:
        return reconstruct_flight_text(html)

    def scrape(self, soup: str, game: Game) -> ScrapedPageInfo:  # type: ignore
        records = self._parse_player_records(soup)
        if not records:
            raise ValueError("Could not find player stats in the flight payload")

        builder = BoxscoreBuilder(game.week)

        for record in records:
            team = NFLTeam.from_abbreviation(record["team"])
            position = record["player"]["position"]
            stats = record["stats"]

            if position == "DEF":
                builder.set_final_score(
                    game.away if game.home == team else game.home,
                    record["stats"]["pts_allow"],
                )
                builder.add_team_defense(team, self._read(stats, DEFENSE_COLUMNS))
            else:
                # `record["player"]["team"]` is sleeper's roster metadata for
                # this player, which can be stale or null (e.g. a recent
                # signing); `team`, resolved above from `record["team"]`, is
                # this game line's actual team and is never missing.

                if position in OFFENSE_POSITIONS:
                    player = self.player_service.get_by_name(
                        record["player"]["first_name"],
                        record["player"]["last_name"],
                        team,
                    )
                    if not player:
                        continue
                    line = self._read(stats, OFFENSE_COLUMNS)
                    two_pt = sum(stats.get(col, 0) for col in TWO_PT_COLUMNS)
                    if two_pt:
                        line[Stat.two_pt_conversions] = two_pt
                    builder.add_player(player, line)
                elif position == "K":
                    player = self.player_service.get_by_name(
                        record["player"]["first_name"],
                        record["player"]["last_name"],
                        team,
                    )
                    if not player:
                        continue
                    builder.add_player(
                        player,
                        stats={Stat.kicking_points: stats.get("kick_pts", 0)},
                    )

        return builder.build(game)

    def _read(
        self, stats: dict[str, Any], columns: dict[Stat, str | list[str]]
    ) -> dict[Stat, float]:
        return {
            stat: (
                stats.get(column, 0)
                if isinstance(column, str)
                else sum([stats.get(inner_col, 0) for inner_col in column])
            )
            for stat, column in columns.items()
        }

    def _parse_game(self, text: str, game_key: str) -> dict[str, Any] | None:
        for game in find_objects(
            text, '"season_type"', ("home_team", "away_team", "home_score", "game_key")
        ):
            if game["game_key"] == game_key:
                return game
        return None

    def _parse_player_records(self, text: str) -> list[dict[str, Any]]:
        """One row per player, deduped, for whichever game has the most stat rows.

        The flight payload prefetches other games too (other matchups on the
        same slate); the game actually being scraped has by far the most
        per-player rows, since every other game is only present as scoreboard
        summary data.
        """
        all_records = find_objects(text, '"category":"stat"', ("player_id", "stats"))

        counts: dict[str, int] = {}
        for record in all_records:
            game_id = record.get("game_id")
            if game_id and game_id != "season":
                counts[game_id] = counts.get(game_id, 0) + 1
        if not counts:
            return []
        game_id = max(counts, key=lambda key: counts[key])

        by_player_id: dict[str, dict[str, Any]] = {}
        for record in all_records:
            if record.get("game_id") != game_id:
                continue
            # the DEF position row duplicates as a "TEAM" row with identical stats
            if record["player"]["position"] == "TEAM":
                continue
            by_player_id[record["player_id"]] = record
        return list(by_player_id.values())
