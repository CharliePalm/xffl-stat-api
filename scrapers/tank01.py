import json
import os
from typing import Any

from curl_cffi import requests

from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.model import Game, NFLTeam, ScrapedPageInfo
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService
from shared.service.service import Criterion

TANK01_HOST = "tank01-fantasy-stats.p.rapidapi.com"

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


class TankScraper(Scraper):
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
        game_id = f"{date}_{game.away.abbreviation}@{game.home.abbreviation}"
        return f"https://{TANK01_HOST}/getNFLBoxScore?gameID={game_id}&fantasyPoints=true&twoPointConversions=2&passYards=.04&passAttempts=0&passTD=4&passCompletions=0&passInterceptions=-2&pointsPerReception=0&carries=.2&rushYards=.1&rushTD=6&fumbles=-2&receivingYards=.1&receivingTD=6&targets=0&defTD=6&fgMade=3&fgMissed=-3&xpMade=1&xpMissed=-1&idpTotalTackles=0&idpSoloTackles=0&idpTFL=0&idpQbHits=0&idpInt=0&idpSacks=0&idpPassDeflections=0&idpFumblesRecovered=0'"

    def parse_html(self, html: str) -> dict[str, Any]:
        return json.loads(html)

    def scrape(self, soup: dict[str, Any], game: Game) -> ScrapedPageInfo:  # type: ignore
        builder = BoxscoreBuilder(game.week)

        builder.set_final_score(game.away, int(soup["awayPts"]))
        builder.set_final_score(game.home, int(soup["homePts"]))
        for side in ("away", "home"):
            dst = soup["DST"][side]
            team = NFLTeam.from_abbreviation(dst["teamAbv"])
            builder.add_team_defense(team, self._read(dst, DEFENSE_COLUMNS))

        for record in soup["playerStats"].values():
            self._add_player(builder, record)

        return builder.build(game)

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

        team = NFLTeam.from_abbreviation(record["teamAbv"])
        player = self.player_service.search(Criterion.eq("tank_id", record["playerID"]))
        if not player:
            print("player not found - ", record["longName"])
            return
        builder.add_player(player, stats)

    def _read(
        self, line: dict[str, Any], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        return {
            stat: float(line.get(column, 0) or 0) for stat, column in columns.items()
        }
