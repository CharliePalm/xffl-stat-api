from abc import ABC, abstractmethod
from typing import TypeVar
from bs4 import BeautifulSoup
from shared.boxscore import Stat
from shared.model import Game, ScrapedPageInfo
from curl_cffi import requests

from shared.parsing import to_float

T = TypeVar("T")


class Scraper[T = BeautifulSoup](ABC):
    dry_run: bool = False
    file_name = "bears_browns.html"

    def get_html(self, url: str | None = None):
        if self.dry_run or not url:
            with open("./test_html/" + self.file_name) as fp:
                return fp.read()
        else:
            res = requests.get(url)
            return res.text

    @staticmethod
    @abstractmethod
    def get_url(game: Game) -> str:
        raise NotImplementedError()

    @abstractmethod
    def parse_html(self, html: str) -> T:
        return BeautifulSoup(html, "html.parser")  # type: ignore

    @abstractmethod
    def scrape(self, soup: T, game: Game) -> ScrapedPageInfo:
        raise NotImplementedError("Scraper.scrape cannot be used generically.")

    def summarize(self, page: ScrapedPageInfo) -> str:
        """Human-readable dump of a scrape, for eyeballing a source's output."""
        lines = [f"{page.game.away.abbreviation} @ {page.game.home.abbreviation}"]
        for player in sorted(page.player_week_data, key=lambda p: -p.points):
            lines.append(f"{player.points:>7.2f} | {player.player_id:<4}")
        return "\n".join(lines)

    def _parse_stat_cols(
        self, line: dict[str, str], columns: dict[Stat, str]
    ) -> dict[Stat, float]:
        # this is for extra points - e.g. 3/3 means 3 xps made of 3 attempted
        clean = lambda x: to_float(x if "/" not in x else x.split("/")[0])
        return {stat: clean(line.get(column)) for stat, column in columns.items()}
