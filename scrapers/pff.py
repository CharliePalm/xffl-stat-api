import json
from typing import Any, Callable
from shared.model import Game, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService

# PFF team abbreviations that differ from what's already stored in xffl.db.
# Extend this if more mismatches show up.
PFF_ABBR_FIXES = {
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "LA": "LAR",
    "WAS": "WSH",
}
# PFF's API gives one flat stat record per player, offense/defense/special
# teams all in the same row (mostly zero except what that player actually did)
OFFENSE_COLUMNS: dict[Stat, str] = {
    Stat.passing_yards: "passing_yards",
    Stat.passing_tds: "passing_touchdowns",
    Stat.interceptions_thrown: "passing_interceptions",
    Stat.rushing_attempts: "rushing_attempts",
    Stat.rushing_yards: "rushing_yards",
    Stat.rushing_tds: "rushing_touchdowns",
    Stat.receptions: "receptions",
    Stat.receiving_yards: "receiving_yards",
    Stat.receiving_tds: "receiving_touchdowns",
    Stat.fumbles_lost: "fumbles_lost",
}
# any of these being nonzero marks a player as fantasy-relevant on offense/kicking;
# everyone else (OL, LS, punter, pure defenders) is skipped as an individual scorer
OFFENSE_SIGNAL_FIELDS = (
    "passing_attempts",
    "rushing_attempts",
    "receiving_targets",
    "field_goals_attempted",
    "extra_points_attempted",
)
# PFF gives no field-goal distance breakdown, so kicking points use the flat
# standard rule (3/FG, 1/XP) rather than a distance-tiered total like ESPN/CBS
FIELD_GOAL_POINTS = 3.0
EXTRA_POINT_POINTS = 1.0

DEFENSE_COLUMNS: dict[Stat, str] = {
    Stat.sacks: "sacks",
    Stat.interceptions: "interceptions",
    Stat.defensive_tds: "defensive_touchdowns",
    Stat.fumbles_recovered: "def_st_fum_rec",
}


class PFFScraper(Scraper[dict[str, Any]]):
    file_name = "lv_hou_pff.json"
    player_service = PlayerService(SessionLocal())

    @staticmethod
    def get_url(game: Game):
        clean: Callable[[str], str] = lambda team_name: team_name.replace(
            " ", "-"
        ).lower()
        home = clean(game.home.full_name)
        away = clean(game.away.full_name)
        week = game.week
        if game.week < 0:
            # preseason test
            week = "P3"
        return f"https://www.pff.com/api/scoreboard/matchup?league=nfl&season={game.season}&week={week}&game={home}_at_{away}_{game.pff_id}"

    def parse_html(self, html: str) -> dict[str, Any]:
        return json.loads(html)

    def scrape(self, soup: dict[str, Any], game: Game) -> ScrapedPageInfo:
        if "away_player_stats" not in soup and "home_player_stats" not in soup:
            return ScrapedPageInfo(game=game, player_week_data=[])
        with open("./pff.json", "w") as fp:
            fp.write(json.dumps(soup))
        away_players = soup["away_player_stats"]
        home_players = soup["home_player_stats"]

        builder = BoxscoreBuilder(game.week)
        builder.set_final_score(game.away, soup["score"]["away_score"])
        builder.set_final_score(game.home, soup["score"]["home_score"])

        for team, players, is_home in (
            (game.away, away_players, False),
            (game.home, home_players, True),
        ):
            defense = {
                stat: sum(player.get(column, 0) for player in players)
                for stat, column in DEFENSE_COLUMNS.items()
            }
            builder.add_team_defense(team, defense)

            for player in players:
                if player.get("field_goals_attempted") or player.get(
                    "extra_points_attempted"
                ):
                    player_obj = self.player_service.get_by_full_name(
                        player.get("name"),
                        game.home if is_home else game.away,
                    )
                    if not player_obj:
                        # print("player not found - ", player.get("name"))
                        continue
                    points = (
                        player.get("field_goals_made", 0) * FIELD_GOAL_POINTS
                        + player.get("extra_points_made", 0) * EXTRA_POINT_POINTS
                    )
                    builder.add_player(
                        player_obj,
                        {Stat.kicking_points: points},
                    )
                elif any(player.get(field) for field in OFFENSE_SIGNAL_FIELDS):
                    player_obj = self.player_service.get_by_full_name(
                        player.get("name"),
                        game.home if is_home else game.away,
                    )
                    if not player_obj:
                        # print("player not found - ", player.get("name"))
                        continue
                    line = {
                        stat: player.get(column, 0)
                        for stat, column in OFFENSE_COLUMNS.items()
                    }
                    builder.add_player(player_obj, line)

        return builder.build(game)
