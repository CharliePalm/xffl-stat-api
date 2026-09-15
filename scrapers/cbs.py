import re

from bs4 import BeautifulSoup, Tag
from shared.model import Game, NFLPosition, NFLTeam, ScrapedPageInfo
from scrapers.scraper import Scraper
from shared.boxscore import BoxscoreBuilder, Stat
from shared.exceptions import DataIntegrityException
from shared.parsing import (
    name_from_url,
    position_from_text,
    team_from_nickname,
    to_float,
)
from shared.service.engine import SessionLocal
from shared.service.player_service import PlayerService

# CBS wraps each stat category in its own "<name>-ctr" div, per team
COLUMNS: dict[str, dict[Stat, str]] = {
    "passing-ctr": {
        Stat.passing_yards: "YDS",
        Stat.passing_tds: "TD",
        Stat.interceptions_thrown: "INT",
        Stat.sack_yards: "SACKS",
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
    "kicking-ctr": {Stat.extra_points_made: "XP", Stat.field_goals_missed: "FG"},
}
# CBS lists per-defender sacks/interceptions here; that's a complete,
# correct team total on its own, with no team-summary table involved —
# summing SACK/INT across every defender already matches the box score.
# fumbles_recovered isn't here at all: "FF" is forced fumbles, a different
# stat (a defender can force a fumble nobody on his team recovers, or
# recover one he didn't force), so it's derived separately, from the
# play-by-play's own recovery text (see `_credit_fumbles_lost`).
DEFENSE_COLUMNS: dict[Stat, str] = {
    Stat.sacks: "SACK",
    Stat.interceptions: "INT",
}

# CBS has no per-player field for a two-point conversion either — it's
# only in the scoring summary's free-text play description, e.g.:
#   "TWO-POINT CONVERSION ATTEMPT. 7-D.Uiagalelei pass to 49-E.Svoboda is
#   complete. ATTEMPT SUCCEEDS."
# Participants are given as "<jersey>-<first initial>.<last name>", not a
# full name, so lookup goes through `get_by_name` with just the initial —
# its last_name+team fallback resolves the player anyway as long as
# there's only one same-surname teammate.
_TEAM_HREF = re.compile(r"/nfl/teams/([A-Z]+)/")
_TWO_POINT_PASS = re.compile(r"\d+-([A-Z])\.(\w+)\s+pass to\s+\d+-([A-Z])\.(\w+)")
_TWO_POINT_RUSH = re.compile(r"\d+-([A-Z])\.(\w+)\s+run\b")

# CBS's scoring summary has no explicit "this was a defensive/special-
# teams touchdown" marker — a return TD is just a "Touchdown" scoring
# item whose free-text description happens to mention the play that
# produced it, e.g. "...INTERCEPTED by 25-J.Colson... for 16 yards
# TOUCHDOWN" or "16-J.Scott punts 52 yards... 6O-J.Cowing for 83 yards
# TOUCHDOWN". None of these words appear in a normal offensive score.
_RETURN_TD_KEYWORDS = ("INTERCEPTED", "punts", "kicks", "FUMBLES")
_FIELD_GOAL_RE = re.compile(
    r"(?:(?:\d+[A-Z]?)-)?([A-Z])\.([A-Za-z]+)\s+(\d+)\s+yard field goal is GOOD",
    re.IGNORECASE,
)


class CBSScraper(Scraper):
    file_name = "sea_tn_cbs.html"
    player_service = PlayerService(SessionLocal())

    @staticmethod
    def get_url(game: Game):
        return game.cbs_link

    @staticmethod
    def get_play_by_play_url(game: Game):
        return game.cbs_link.replace("boxscore", "playbyplay")

    def get_play_by_play_html(self, game: Game) -> str:
        return self.get_html(self.get_play_by_play_url(game))

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
                    player = self.player_service.get_by_full_name(name, team)
                    if player is None:
                        print("not found: ", (team, name))
                        continue
                    res = self._parse_stat_cols(line, columns)
                    builder.add_player(player, res)
            for team, _name, _pos, line in self._parse_section(
                container, "defense-ctr"
            ):
                builder.add_team_defense(
                    team, self._parse_stat_cols(line, DEFENSE_COLUMNS)
                )

        for team, description in self._parse_two_point_conversions(soup):
            self._credit_two_point_conversion(builder, team, description)

        for team in self._parse_return_touchdowns(soup):
            builder.add_team_defense(team, {Stat.defensive_tds: 1})

        play_by_play = BeautifulSoup(self.get_play_by_play_html(game), "html.parser")
        self._credit_fumbles_lost(builder, play_by_play, game)
        self._credit_field_goals(builder, play_by_play, game)
        return builder.build(game)

    def _parse_return_touchdowns(self, soup: BeautifulSoup) -> list[NFLTeam]:
        """One entry per defensive/special-teams touchdown in the scoring
        summary — a punt, kickoff, interception, or fumble return TD."""
        teams: list[NFLTeam] = []
        for item in soup.find_all(class_="scoring_item"):
            result = item.find(class_="result_str")
            if not isinstance(result, Tag):
                continue
            if result.get_text(strip=True).lower() != "touchdown":
                continue

            description_el = item.find(class_="last_play_description")
            description = (
                description_el.get_text(" ", strip=True) if description_el else ""
            )
            if not any(keyword in description for keyword in _RETURN_TD_KEYWORDS):
                continue

            link = item.find("a", href=_TEAM_HREF)
            team_match = (
                _TEAM_HREF.search(link["href"]) if isinstance(link, Tag) else None
            )
            if not team_match:
                continue
            teams.append(NFLTeam.from_abbreviation(team_match.group(1)))
        return teams

    def _parse_two_point_conversions(
        self, soup: BeautifulSoup
    ) -> list[tuple[NFLTeam, str]]:
        """One entry per made two-point conversion in the scoring summary,
        as (scoring team, the play's free-text description)."""
        conversions: list[tuple[NFLTeam, str]] = []
        for item in soup.find_all(class_="scoring_item"):
            result = item.find(class_="result_str")
            if not isinstance(result, Tag):
                continue
            if result.get_text(strip=True).lower() != "two point conversion":
                continue

            description_el = item.find(class_="last_play_description")
            description = (
                description_el.get_text(" ", strip=True) if description_el else ""
            )
            if "ATTEMPT SUCCEEDS" not in description.upper():
                continue  # a failed attempt earns nobody anything

            link = item.find("a", href=_TEAM_HREF)
            team_match = (
                _TEAM_HREF.search(link["href"]) if isinstance(link, Tag) else None
            )
            if not team_match:
                continue
            conversions.append(
                (NFLTeam.from_abbreviation(team_match.group(1)), description)
            )
        return conversions

    def _credit_two_point_conversion(
        self, builder: BoxscoreBuilder, team: NFLTeam, description: str
    ) -> None:
        pass_match = _TWO_POINT_PASS.search(description)
        if pass_match:
            participants = [
                (pass_match.group(1), pass_match.group(2)),
                (pass_match.group(3), pass_match.group(4)),
            ]
        else:
            rush_match = _TWO_POINT_RUSH.search(description)
            participants = (
                [(rush_match.group(1), rush_match.group(2))] if rush_match else []
            )

        for first_initial, last_name in participants:
            try:
                # only an initial, not a full first name — get_by_name's
                # last_name+team fallback resolves this anyway
                player = self.player_service.get_by_name(first_initial, last_name, team)
            except DataIntegrityException:
                print("not found: ", (team, first_initial, last_name))
                continue
            builder.add_player(player, {Stat.two_pt_conversions: 1})

    def _credit_fumbles_lost(
        self, builder: BoxscoreBuilder, soup: BeautifulSoup, game: Game
    ) -> None:
        for row in soup.find_all("tr", class_="TableBase-bodyTr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            text = cells[1].get_text(" ", strip=True)
            reversed_idx = text.find("REVERSED")
            if reversed_idx != -1:
                text = text[reversed_idx:]
            idx = text.find("FUMBLES")
            if idx == -1:
                continue
            before, after = text[:idx], text[idx:]

            # Strip parentheticals (e.g. "(C.Young)") so tacklers/forcers
            # aren't mistaken for the fumbler
            before_clean = re.sub(r"\([^)]*\)", "", before)
            names = re.findall(r"[A-Z]\.[A-Z][a-zA-Z']+", before_clean)
            fumbled_by = names[-1] if names else None

            m = re.search(r"(?:recovered|RECOVERED)\s+by\s+([\w.'\-]+)", after)
            recovered_by = m.group(1) if m else None

            if not fumbled_by or not recovered_by:
                print("this should never happen")
                continue
            first_name, last_name = fumbled_by.split(".")
            player = self.player_service.get_by_name(
                first_name, last_name, [game.home, game.away]
            )
            if not player:
                print("not found: ", text)
                print(player)
                continue

            fumbler_team = player.team

            recovering_team = NFLTeam(recovered_by.split("-")[0])

            if recovering_team == fumbler_team:
                continue  # recovered by their own team - not lost

            builder.add_player(player, {Stat.fumbles_lost: 1})
            builder.add_team_defense(
                recovering_team,
                {Stat.fumbles_recovered: 1},
            )

    def _credit_field_goals(
        self, builder: BoxscoreBuilder, play_by_play: BeautifulSoup, game: Game
    ) -> None:
        for item in play_by_play.find_all(class_="scoring_item"):
            result = item.find(class_="result_str")
            if not isinstance(result, Tag):
                continue
            if result.get_text(strip=True).lower() != "field goal":
                continue

            description_el = item.find(class_="last_play_description")
            description = (
                description_el.get_text(" ", strip=True) if description_el else ""
            )
            match = _FIELD_GOAL_RE.search(description)
            if not match:
                continue

            first_initial, last_name, yards = match.groups()
            team_link = item.find("a", href=_TEAM_HREF)
            team_match = (
                _TEAM_HREF.search(team_link["href"])
                if isinstance(team_link, Tag)
                else None
            )
            if not team_match:
                continue
            team = NFLTeam.from_abbreviation(team_match.group(1))

            try:
                kicker = self.player_service.get_by_name(first_initial, last_name, team)
            except DataIntegrityException:
                continue

            builder.add_field_goal(kicker, int(yards))

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
