import json
import os
import re
from typing import Any

from curl_cffi import requests

from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.model import Game, NFLTeam, ScrapedPageInfo
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService
from shared.service.service import Criterion

TANK01_HOST = "tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"

# tank01 team abbreviations that differ from this app's canonical NFLTeam
# abbreviation (e.g. tank01's "WSH" vs our "WAS")
TEAM_ABBR_FIXES = {"WSH": "WAS"}
_REVERSE_TEAM_ABBR_FIXES = {ours: theirs for theirs, ours in TEAM_ABBR_FIXES.items()}


def _team(abbreviation: str) -> NFLTeam:
    return NFLTeam.from_abbreviation(TEAM_ABBR_FIXES.get(abbreviation, abbreviation))


def _tank01_abbreviation(team: NFLTeam) -> str:
    return _REVERSE_TEAM_ABBR_FIXES.get(team.abbreviation, team.abbreviation)


# tank01 category -> the raw stat keys we take from that category
OFFENSE_COLUMNS: dict[str, dict[Stat, str]] = {
    "Passing": {
        Stat.passing_yards: "passYds",
        Stat.passing_tds: "passTD",
        Stat.interceptions_thrown: "int",
    },
    "Rushing": {
        Stat.rushing_attempts: "carries",
        Stat.rushing_yards: "rushYds",
        Stat.rushing_tds: "rushTD",
    },
    "Receiving": {
        Stat.receptions: "receptions",
        Stat.receiving_yards: "recYds",
        Stat.receiving_tds: "recTD",
    },
}
# a player who fumbled (offense or special teams) carries it on their own
# "Defense" category rather than on the category the fumble happened in
FUMBLES_LOST_COLUMN = "fumblesLost"
# team totals, already computed server-side — no per-defender accumulation needed
DEFENSE_COLUMNS: dict[Stat, str] = {
    Stat.sacks: "sacks",
    Stat.interceptions: "defensiveInterceptions",
    Stat.fumbles_recovered: "fumblesRecovered",
    Stat.defensive_tds: "defTD",
    Stat.safeties: "safeties",
}

# tank01 has no per-player field for a two-point conversion anywhere in
# `playerStats` — the only place it shows up is buried in a scoring play's
# free-text description, e.g.:
#
#   "Jerand Bradley 28 Yd pass from DJ Uiagalelei (DJ Uiagalelei Pass to
#   Evan Svoboda for Two-Point Conversion)"
#
# so both the passer and receiver (or the sole rusher, for a run) have to
# be pulled out of that text rather than read off a column.
_TWO_POINT_PASS = re.compile(
    r"\(([^()]+?)\s+Pass to\s+([^()]+?)\s+for Two-Point Conversion\)", re.IGNORECASE
)
_TWO_POINT_RUSH = re.compile(
    r"\(([^()]+?)\s+Run for Two-Point Conversion\)", re.IGNORECASE
)

# tank01's `DST.defTD` undercounts: it reflects an interception-return TD
# (e.g. "Junior Colson 16 Yd Interception Return" -> LAC's defTD is 1) but
# not a punt/kick/fumble return TD — SF's "Jacob Cowing 83 Yd Punt Return"
# TD here isn't in SF's defTD (0) at all. Scanning scoringPlays' free text
# for those return types and crediting the scoring team's defense fills
# in exactly what defTD misses, without double-counting the INT case it
# already has covered.
_RETURN_TD_KEYWORDS = ("Punt Return", "Kick Return", "Fumble Return")


class TankScraper(Scraper[dict[str, Any]]):
    file_name = "tank01.json"
    player_service = PlayerService(SessionLocal())

    def get_html(self, url: str | None = None):
        if self.dry_run or not url:
            return super().get_html(url)
        res = requests.get(
            url,
            headers={
                "x-rapidapi-key": os.environ.get("RAPIDAPI_KEY", ""),
                "x-rapidapi-host": TANK01_HOST,
            },
        )
        return res.text

    @staticmethod
    def get_url(game: Game) -> str:
        date = game.date_time.split(" ")[0].replace("-", "")
        away = _tank01_abbreviation(game.away)
        home = _tank01_abbreviation(game.home)
        game_id = f"{date}_{away}@{home}"
        return f"https://{TANK01_HOST}/getNFLBoxScore?gameID={game_id}&fantasyPoints=true&twoPointConversions=2&passYards=.04&passAttempts=0&passTD=4&passCompletions=0&passInterceptions=-2&pointsPerReception=0&carries=.2&rushYards=.1&rushTD=6&fumbles=-2&receivingYards=.1&receivingTD=6&targets=0&defTD=6&fgMade=3&fgMissed=-3&xpMade=1&xpMissed=-1&idpTotalTackles=0&idpSoloTackles=0&idpTFL=0&idpQbHits=0&idpInt=0&idpSacks=0&idpPassDeflections=0&idpFumblesRecovered=0'"

    def parse_html(self, html: str) -> dict[str, Any]:
        # RapidAPI wraps the actual box score in a Lambda-style envelope
        data = json.loads(html)
        return data["body"] if "body" in data else data

    def scrape(self, soup: dict[str, Any], game: Game) -> ScrapedPageInfo:  # type: ignore
        builder = BoxscoreBuilder(game.week)

        builder.set_final_score(game.away, int(soup["awayPts"]))
        builder.set_final_score(game.home, int(soup["homePts"]))
        for side in ("away", "home"):
            dst = soup["DST"][side]
            team = _team(dst["teamAbv"])
            builder.add_team_defense(team, self._read(dst, DEFENSE_COLUMNS))

        for record in soup["playerStats"].values():
            self._add_player(builder, record)

        for play in soup.get("scoringPlays", []):
            self._credit_two_point_conversion(builder, play)
            self._credit_return_touchdown(builder, play)

        return builder.build(game)

    def _credit_return_touchdown(
        self, builder: BoxscoreBuilder, play: dict[str, Any]
    ) -> None:
        if play.get("scoreType") != "TD":
            return
        description = play.get("score", "")
        if not any(keyword in description for keyword in _RETURN_TD_KEYWORDS):
            return
        builder.add_team_defense(_team(play["team"]), {Stat.defensive_tds: 1})

    def _credit_two_point_conversion(
        self, builder: BoxscoreBuilder, play: dict[str, Any]
    ) -> None:
        description = play.get("score", "")
        if "Two-Point Conversion" not in description:
            return

        pass_match = _TWO_POINT_PASS.search(description)
        if pass_match:
            names = [pass_match.group(1), pass_match.group(2)]
        else:
            rush_match = _TWO_POINT_RUSH.search(description)
            names = [rush_match.group(1)] if rush_match else []

        # both players are on the scoring team, not necessarily in
        # `playerStats` (tank01 never assigned Evan Svoboda a player id
        # here at all) or in `playerIDs` (which only names the touchdown's
        # participants, not the conversion's) — name + team is all we have
        team = _team(play["team"])
        for name in names:
            player = self.player_service.get_by_full_name(name.strip(), team)
            if not player:
                print("player not found - ", name)
                continue
            builder.add_player(player, {Stat.two_pt_conversions: 1})

    def _add_player(self, builder: BoxscoreBuilder, record: dict[str, Any]) -> None:
        stats: dict[Stat, float] = {}
        for category, columns in OFFENSE_COLUMNS.items():
            if category in record:
                stats.update(self._read(record[category], columns))

        kicking = record.get("Kicking")
        if kicking and "kickingPts" in kicking:
            stats[Stat.kicking_points] = float(kicking["kickingPts"])

        if not stats:
            return

        defense = record.get("Defense")
        if defense and FUMBLES_LOST_COLUMN in defense:
            stats[Stat.fumbles_lost] = float(defense[FUMBLES_LOST_COLUMN])

        matches = self.player_service.search(
            Criterion.eq("tank_id", record["playerID"])
        ).items
        if not matches:
            print("player not found - ", record["longName"])
            return
        builder.add_player(matches[0], stats)

    def _read(
        self, line: dict[str, Any], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        return {
            stat: float(line.get(column, 0) or 0) for stat, column in columns.items()
        }
