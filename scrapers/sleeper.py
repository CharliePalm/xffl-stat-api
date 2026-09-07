from typing import Any

from shared.db import Database
from shared.model import Game, NFLPosition, NFLTeam, Player, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.next_flight import find_objects, reconstruct_flight_text

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
DEFENSE_COLUMNS: dict[Stat, str] = {
    Stat.sacks: "sack",
    Stat.interceptions: "int",
    Stat.fumbles_recovered: "fum_rec",
    Stat.defensive_tds: "def_td",
}


class SleeperScraper(Scraper):
    file_name = "9ers_chargers_sleeper.html"
    _db = Database()

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

    def scrape(self, soup: str, week: int) -> ScrapedPageInfo:
        records = self._parse_player_records(soup)
        if not records:
            raise ValueError("Could not find player stats in the flight payload")
        game = self._parse_game(soup, records[0]["game_id"])
        if game is None:
            raise ValueError("Could not find game metadata in the flight payload")
        home_team = NFLTeam.from_abbreviation(game["home_team"])
        away_team = NFLTeam.from_abbreviation(game["away_team"])

        builder = BoxscoreBuilder(week)
        builder.set_final_score(home_team, game["home_score"])
        builder.set_final_score(away_team, game["away_score"])

        for record in records:
            team = NFLTeam.from_abbreviation(record["team"])
            position = record["player"]["position"]
            stats = record["stats"]

            if position == "DEF":
                builder.add_team_defense(team, self._read(stats, DEFENSE_COLUMNS))
            else:
                player = self._db.fetch_model(
                    "select * from player where id = ?", Player, record["player"]["id"]
                )
                if position in OFFENSE_POSITIONS:
                    line = self._read(stats, OFFENSE_COLUMNS)
                    two_pt = sum(stats.get(col, 0) for col in TWO_PT_COLUMNS)
                    if two_pt:
                        line[Stat.two_pt_conversions] = two_pt
                    builder.add_player(team, name, line, NFLPosition(position))
                elif position == "K":
                    builder.add_player(
                        player,
                        stats={Stat.kicking_points: stats.get("kick_pts", 0)},
                    )

        return builder.build(home_team, away_team)

    def _read(
        self, stats: dict[str, Any], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        return {stat: stats.get(column, 0) for stat, column in columns.items()}

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


if __name__ == "__main__":
    scraper = SleeperScraper()
    parsed = scraper.parse_html(scraper.get_html())
    print(scraper.summarize(scraper.scrape(parsed, week=2)))
