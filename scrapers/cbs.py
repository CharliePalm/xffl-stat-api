from bs4 import BeautifulSoup, Tag

from shared.db import Database
from shared.model import Game, NFLPosition, NFLTeam, Player, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.parsing import (
    name_from_url,
    position_from_text,
    team_from_nickname,
    to_float,
)

# CBS wraps each stat category in its own "<name>-ctr" div, per team
COLUMNS: dict[str, dict[Stat, str]] = {
    "passing-ctr": {
        Stat.passing_yards: "YDS",
        Stat.passing_tds: "TD",
        Stat.interceptions_thrown: "INT",
    },
    "rushing-ctr": {
        Stat.rushing_attempts: "ATT",
        Stat.rushing_yards: "YDS",
        Stat.rushing_tds: "TD",
    },
    "receiving-ctr": {
        Stat.receptions: "REC",
        Stat.receiving_yards: "YDS",
        Stat.receiving_tds: "TD",
    },
    "kicking-ctr": {Stat.kicking_points: "PTS"},
}
# CBS lists per-defender stats here. Team-summary rows in the side panel are the
# place where return TDs / recovered fumbles / interception returns are counted.
DEFENSE_COLUMNS: dict[Stat, str] = {
    Stat.sacks: "SACK",
    Stat.interceptions: "INT",
    Stat.fumbles_recovered: "FF",
}


class CBSScraper(Scraper):
    file_name = "sea_tn_cbs.html"
    _db = Database()

    @staticmethod
    def get_url(game: Game):
        return game.cbs_link

    def parse_html(self, html: str) -> BeautifulSoup:
        with open("./cbs.html", "w") as fp:
            fp.write(html)
        return BeautifulSoup(html, "html.parser")

    def scrape(self, soup: BeautifulSoup, game: Game) -> ScrapedPageInfo:  # type: ignore
        builder = BoxscoreBuilder(game.week)

        linescore = self._parse_linescore(soup)
        if len(linescore) != 2:
            raise ValueError(f"Expected two teams in the linescore, got {linescore}")
        # CBS lists the away team in the first linescore row
        (away_team, away_points), (home_team, home_points) = linescore
        builder.set_final_score(away_team, away_points)
        builder.set_final_score(home_team, home_points)

        for container in soup.find_all(class_="stats-ctr-container"):
            for section, columns in COLUMNS.items():
                for team, name, _pos, line in self._parse_section(container, section):
                    player = self._db.get_player_by_full_name(name, team)
                    if player is None:
                        print("not found: ", (team, name))
                        continue
                    builder.add_player(player, self._read(line, columns))
            for team, _name, _pos, line in self._parse_section(
                container, "defense-ctr"
            ):
                builder.add_team_defense(team, self._read(line, DEFENSE_COLUMNS))

        self._add_team_summary_defense_totals(soup, away_team, home_team, builder)

        return builder.build(game)

    def _add_team_summary_defense_totals(
        self,
        soup: BeautifulSoup,
        away_team: NFLTeam,
        home_team: NFLTeam,
        builder: BoxscoreBuilder,
    ) -> None:
        table = soup.find("table", class_="team-stats")
        if not isinstance(table, Tag):
            return

        explicit_td_by_team: dict[NFLTeam, float] = {away_team: 0.0, home_team: 0.0}
        fallback_td_by_team: dict[NFLTeam, float] = {away_team: 0.0, home_team: 0.0}

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            label = cells[0].get_text(" ", strip=True)
            if label not in {"Other", "Int. - Returns", "Fumbles - Lost"}:
                continue

            values = [cell.get_text(" ", strip=True) for cell in cells[1:3]]
            away_value = self._team_summary_value(values[0])
            home_value = self._team_summary_value(values[1])

            if label == "Other":
                fallback_td_by_team[away_team] += away_value
                fallback_td_by_team[home_team] += home_value
                continue

            if label == "Int. - Returns":
                builder.add_team_defense(away_team, {Stat.interceptions: away_value})
                builder.add_team_defense(home_team, {Stat.interceptions: home_value})
                explicit_td_by_team[away_team] += away_value
                explicit_td_by_team[home_team] += home_value
                continue

            if label == "Fumbles - Lost":
                builder.add_team_defense(away_team, {Stat.fumbles_recovered: away_value})
                builder.add_team_defense(home_team, {Stat.fumbles_recovered: home_value})
                explicit_td_by_team[away_team] += away_value
                explicit_td_by_team[home_team] += home_value

        for team in (away_team, home_team):
            td_total = explicit_td_by_team[team]
            if td_total == 0:
                td_total = fallback_td_by_team[team]
            if td_total > 0:
                builder.add_team_defense(team, {Stat.defensive_tds: td_total})

    @staticmethod
    def _team_summary_value(raw: str) -> float:
        text = raw.strip()
        if "-" in text:
            left, _right = text.split("-", 1)
            if left.strip():
                return float(to_float(left.strip()))
        return float(to_float(text))

    def _read(
        self, line: dict[str, str], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        return {stat: to_float(line.get(column)) for stat, column in columns.items()}

    def _parse_linescore(self, soup: BeautifulSoup) -> list[tuple[NFLTeam, int]]:
        table = soup.find("table", class_="linescore")
        if not isinstance(table, Tag):
            return []
        rows: list[tuple[NFLTeam, int]] = []
        for tr in table.find_all("tr"):
            cells = [td.text.strip() for td in tr.find_all("td")]
            if len(cells) < 2:
                continue
            # first cell is e.g. "Seahawks\n0-2" — nickname above the win-loss record;
            # the quarter-header row has an empty first cell and is skipped
            label = cells[0].splitlines()
            team = team_from_nickname(label[0]) if label else None
            if team is None:
                continue
            rows.append((team, int(to_float(cells[-1]))))
        return rows

    def _parse_section(
        self, container: Tag, section: str
    ) -> list[tuple[NFLTeam, str, NFLPosition | None, dict[str, str]]]:
        """Read one team's rows for one stat category out of its container div."""
        section_div = container.find(class_=section)
        if not isinstance(section_div, Tag):
            return []

        # the category's header row lives in a separate table from the data rows
        header_row = section_div.find("tr", class_="header-row")
        if not isinstance(header_row, Tag):
            return []
        headers = [td.text.strip() for td in header_row.find_all("td")]

        rows: list[tuple[NFLTeam, str, NFLPosition | None, dict[str, str]]] = []
        for tr in section_div.find_all("tr", class_="data-row"):
            name_cell = tr.find("td", class_="name-element")
            hover = tr.find("td", class_="hover-element")
            if not isinstance(name_cell, Tag) or not isinstance(hover, Tag):
                continue
            # the hover card carries the authoritative team and position
            team = NFLTeam.from_abbreviation(str(hover.get("data-team-abbr") or ""))
            num_pos = hover.find(class_="num-pos")
            pos = position_from_text(num_pos.text) if isinstance(num_pos, Tag) else None

            link = name_cell.find("a")
            href = link.get("href") if isinstance(link, Tag) else None
            name = name_from_url(
                href if isinstance(href, str) else None, name_cell.text.strip()
            )

            # headers[0] labels the name column, so the numbers line up after it
            values = [
                td.text.strip() for td in tr.find_all("td", class_="number-element")
            ]
            rows.append((team, name, pos, dict(zip(headers[1:], values))))
        return rows


if __name__ == "__main__":
    scraper = CBSScraper()
    print(
        scraper.summarize(
            scraper.scrape(
                scraper.parse_html(
                    scraper.get_html(
                        "https://www.cbssports.com/nfl/gametracker/boxscore/NFL_20260823_SEA@TEN/"
                    )
                ),
                1,
            )
        )
    )
