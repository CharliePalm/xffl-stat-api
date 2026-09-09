import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag
from shared.model import Game, NFLTeam, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.parsing import name_from_url, team_from_nickname, to_float
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService

# the trailing row of every ESPN table holds team totals, not a player
TEAM_TOTALS_LABEL = "team"

# section title stat -> the stats we take from that table's columns
COLUMNS: dict[str, dict[Stat, str]] = {
    "Passing": {
        Stat.passing_yards: "YDS",
        Stat.passing_tds: "TD",
        Stat.interceptions_thrown: "INT",
    },
    "Rushing": {
        Stat.rushing_attempts: "CAR",
        Stat.rushing_yards: "YDS",
        Stat.rushing_tds: "TD",
    },
    "Receiving": {
        Stat.receptions: "REC",
        Stat.receiving_yards: "YDS",
        Stat.receiving_tds: "TD",
    },
    "Fumbles": {Stat.fumbles_lost: "LOST"},
    "Kicking": {Stat.kicking_points: "PTS"},
}
# sections whose team-totals row feeds D/ST rather than individual players.
# "Defense"'s own TD column is already that team's *total* defensive/
# special-teams TD count (it's what carries a punt- or kick-return TD,
# which has no section of its own) — an interception-return TD shows up
# there too, and is then broken out again in "Interceptions"' own TD
# column, so reading defensive_tds from both double-counts a pick-six.
DEFENSE_COLUMNS: dict[str, dict[Stat, str]] = {
    "Defense": {Stat.sacks: "SACKS", Stat.defensive_tds: "TD"},
    "Interceptions": {Stat.interceptions: "INT"},
}

# ESPN has no per-player field for a two-point conversion either — it's
# only in the free-text `playText` of the touchdown it rode in on, e.g.
# "Jerand Bradley 28 Yd pass from DJ Uiagalelei (DJ Uiagalelei Pass to
# Evan Svoboda for Two-Point Conversion)", buried inside a big JSON blob
# the boxscore page doesn't carry at all — it's only on the separate
# play-by-play page (`get_play_by_play_url`), embedded as
# `window['__espnfitt__'] = {...};`.
_ESPN_FITT_DATA = re.compile(
    r"window\['__espnfitt__'\]\s*=\s*(\{.*?\});\s*</script>", re.DOTALL
)
_TWO_POINT_PASS = re.compile(
    r"\(([^()]+?)\s+Pass to\s+([^()]+?)\s+for Two-Point Conversion\)", re.IGNORECASE
)
_TWO_POINT_RUSH = re.compile(
    r"\(([^()]+?)\s+Run for Two-Point Conversion\)", re.IGNORECASE
)


class ESPNScraper(Scraper):
    file_name = "espn.json"
    player_service = PlayerService(SessionLocal())

    @staticmethod
    def get_url(game: Game):
        return game.espn_link

    @staticmethod
    def get_play_by_play_url(game: Game):
        return game.espn_link.replace("boxscore", "playbyplay")

    def get_play_by_play_html(self, game: Game) -> str:
        return self.get_html(self.get_play_by_play_url(game))

    def parse_html(self, html: str) -> BeautifulSoup:
        with open("./epsn.html", "w") as fp:
            fp.write(html)
        return BeautifulSoup(html, "html.parser")

    def scrape(self, soup: BeautifulSoup, game: Game) -> ScrapedPageInfo:  # type: ignore
        builder = BoxscoreBuilder(game.week)

        linescore = self._parse_linescore(soup)
        if len(linescore) != 2:
            raise ValueError(f"Expected two teams in the linescore, got {linescore}")
        # ESPN lists the away team in the first linescore row
        (away_team, away_points), (home_team, home_points) = linescore
        builder.set_final_score(away_team, away_points)
        builder.set_final_score(home_team, home_points)

        sections = self._parse_sections(soup)
        for team, stat, rows in sections:
            for name, line in rows.items():
                is_totals = name.lower() == TEAM_TOTALS_LABEL
                if is_totals and stat in DEFENSE_COLUMNS:
                    builder.add_team_defense(
                        team, self._read(line, DEFENSE_COLUMNS[stat])
                    )
                if not is_totals and stat in COLUMNS:
                    player = self.player_service.get_by_full_name(name, team)
                    if not player:
                        print("player not found - ", name)
                        continue
                    builder.add_player(player, self._read(line, COLUMNS[stat]))

        self._credit_fumbles_recovered(builder, sections)

        for team, description in self._parse_two_point_conversions(
            self.get_play_by_play_html(game)
        ):
            self._credit_two_point_conversion(builder, team, description)

        return builder.build(game)

    def _credit_fumbles_recovered(
        self,
        builder: BoxscoreBuilder,
        sections: list[tuple[NFLTeam, str, dict[str, dict[str, str]]]],
    ) -> None:
        """A team's own "Fumbles" section total isn't usable for its
        defensive `fumbles_recovered` — it sums every recovery regardless
        of whose ball it was, including a player recovering his *own*
        team's fumble (not a takeaway). A fumble that's actually lost
        (`LOST` on the fumbling player's row) was necessarily recovered by
        the opposing team, so that team's takeaway total is the *other*
        team's lost-fumble total, not its own section's `REC` column."""
        fumbles_lost_by_team: dict[NFLTeam, float] = {}
        for team, stat, rows in sections:
            if stat != "Fumbles":
                continue
            fumbles_lost_by_team[team] = sum(
                to_float(line.get("LOST"))
                for name, line in rows.items()
                if name.lower() != TEAM_TOTALS_LABEL
            )

        teams = list(fumbles_lost_by_team)
        if len(teams) != 2:
            return
        for team, opponent in ((teams[0], teams[1]), (teams[1], teams[0])):
            builder.add_team_defense(
                team, {Stat.fumbles_recovered: fumbles_lost_by_team[opponent]}
            )

    def _parse_two_point_conversions(self, html: str) -> list[tuple[NFLTeam, str]]:
        """Two-point conversions live only on the play-by-play page, buried
        in a scoring play's free-text `playText` — not on the boxscore page
        `scrape()` otherwise works from at all."""
        match = _ESPN_FITT_DATA.search(html)
        if not match:
            return []
        data: dict[str, Any] = json.loads(match.group(1))
        scoring_groups = (
            data.get("page", {})
            .get("content", {})
            .get("gamepackage", {})
            .get("pbp", {})
            .get("scoringPlaysData", [])
        )

        conversions: list[tuple[NFLTeam, str]] = []
        for group in scoring_groups:
            for play in group.get("items", []):
                text = play.get("playText", "")
                if "Two-Point Conversion" not in text:
                    continue
                team_name = play.get("teamName")
                if not team_name:
                    continue
                conversions.append((NFLTeam(team_name), text))
        return conversions

    def _credit_two_point_conversion(
        self, builder: BoxscoreBuilder, team: NFLTeam, description: str
    ) -> None:
        pass_match = _TWO_POINT_PASS.search(description)
        if pass_match:
            names = [pass_match.group(1), pass_match.group(2)]
        else:
            rush_match = _TWO_POINT_RUSH.search(description)
            names = [rush_match.group(1)] if rush_match else []

        for name in names:
            player = self.player_service.get_by_full_name(name.strip(), team)
            if not player:
                print("player not found - ", name)
                continue
            builder.add_player(player, {Stat.two_pt_conversions: 1})

    def _read(
        self, line: dict[str, str], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        return {stat: to_float(line.get(column)) for stat, column in columns.items()}

    def _parse_linescore(self, soup: BeautifulSoup) -> list[tuple[NFLTeam, int]]:
        table = soup.find(attrs={"data-testid": "prism-Table"})  # type: ignore[arg-type]
        if not isinstance(table, Tag):
            return []
        headers = [th.text.strip() for th in table.find_all("th")]
        tbody = table.find("tbody")
        if not isinstance(tbody, Tag):
            return []
        rows: list[tuple[NFLTeam, int]] = []
        for tr in tbody.find_all("tr"):
            cells = [td.text.strip() for td in tr.find_all("td")]
            if not cells:
                continue
            # first cell is e.g. "BrownsCLE" — the trailing abbreviation names the team
            abbr = re.search(r"([A-Z]{2,3})$", cells[0])
            if not abbr:
                continue
            team = NFLTeam.from_abbreviation(abbr.group(1))
            totals = dict(zip(headers[1:], cells[1:]))
            rows.append((team, int(to_float(totals.get("T")))))
        return rows

    def _parse_sections(
        self, soup: BeautifulSoup
    ) -> list[tuple[NFLTeam, str, dict[str, dict[str, str]]]]:
        """One entry per (team, stat category), mapping player name -> raw stat cells."""
        sections: list[tuple[NFLTeam, str, dict[str, dict[str, str]]]] = []
        for team_div in soup.find_all(class_="Boxscore__Team"):
            title_el = team_div.find(attrs={"data-testid": "teamTitle"})  # type: ignore[arg-type]
            if not isinstance(title_el, Tag):
                continue
            # e.g. "Cleveland Passing" — the city prefix is ambiguous for shared
            # markets, so take the team from the logo's alt text ("Browns") instead
            _, _, stat = title_el.text.strip().rpartition(" ")
            team = self._parse_team(team_div)
            if team is None or (stat not in COLUMNS and stat not in DEFENSE_COLUMNS):
                continue
            sections.append((team, stat, self._parse_section_rows(team_div)))
        return sections

    def _parse_section_rows(self, team_div: Tag) -> dict[str, dict[str, str]]:
        """ESPN splits each section into a names table and a parallel stats table."""
        headers: list[str] = []
        names: list[str] = []
        stat_rows: list[list[str]] = []

        for tbl in team_div.find_all("table"):
            table_headers = [
                th.text.strip() for th in tbl.find_all("th") if th.text.strip()
            ]
            if table_headers:
                headers = table_headers
            for tr in tbl.find_all("tr"):
                cells = tr.find_all("td")
                if not cells:
                    continue
                if len(cells) == 1:
                    # the cell text carries a jersey number ("Deshaun Watson#4"), so
                    # prefer the profile link's slug for a clean, cross-source name
                    label = cells[0].text.strip()
                    if label.lower() == TEAM_TOTALS_LABEL:
                        names.append(label)
                        continue
                    link = cells[0].find("a")
                    href = link.get("href") if isinstance(link, Tag) else None
                    names.append(
                        name_from_url(href if isinstance(href, str) else None, label)
                    )
                else:
                    stat_rows.append([td.text.strip() for td in cells])

        rows: dict[str, dict[str, str]] = {}
        for name, cells in zip(names, stat_rows):
            # the defense table prefixes group headers ("TACKLES", "MISC") that have
            # no cells of their own, so align headers to the row's right edge
            rows[name] = dict(zip(headers[-len(cells) :], cells))
        return rows

    def _parse_team(self, team_div: Tag) -> NFLTeam | None:
        for img in team_div.find_all("img"):
            team = team_from_nickname(str(img.get("alt") or ""))
            if team:
                return team
        return None
