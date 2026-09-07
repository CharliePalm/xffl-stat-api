from typing import Any, Callable

from scrapers.scraper import Scraper
from shared.db import Database
from shared.model import Game, Provider
from scrapers import espn, cbs, pff, sleeper

providers: dict[str, Callable[[], Scraper]] = {
    "cbs": lambda: cbs.CBSScraper(),
    "espn": lambda: espn.ESPNScraper(),
    "pff": lambda: pff.PFFScraper(),
    "sleeper": lambda: sleeper.SleeperScraper(),
    # 'nbc': lambda _: cbs.CBSScraper(),
}


class ScrapeManager:
    def __init__(self):
        self.db = Database()

    def pick_provider(self):
        provider = self.db.fetch_model(
            "select * from provider order by pos asc limit 1;", Provider
        )
        if not provider:
            raise Exception("no provider returned from query")
        return provider

    def run(self, game: Game):
        # provider = self.pick_provider()
        provider = Provider(name="sleeper", pos=0)
        scraper = providers[provider.name]()
        print(scraper.get_url(game))
        return
        html = scraper.get_html(scraper.get_url(game))
        print(html)
        return
        res = scraper.scrape(scraper.parse_html(html), game)
        print(res)
        for player_week in res.player_week_data:
            self.db.write_model(player_week)
        provider.pos = provider.pos + len(providers)
        self.db.write_model(provider)


if __name__ == "__main__":
    m = ScrapeManager()
    game = m.db.fetch_model("select * from game limit 1", Game)
    m.run(game)
