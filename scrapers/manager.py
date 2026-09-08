from typing import Any, Callable

from scrapers.scraper import Scraper
from shared.db.db import Database
from shared.model import Game, Provider
from scrapers import espn, cbs, pff, sleeper
from shared.service.engine import SessionLocal
from shared.service.provider import ProviderService
from shared.service.service import Sort

providers: dict[str, Callable[[], Scraper]] = {
    "cbs": lambda: cbs.CBSScraper(),
    "espn": lambda: espn.ESPNScraper(),
    "pff": lambda: pff.PFFScraper(),
    "sleeper": lambda: sleeper.SleeperScraper(),
    # 'nbc': lambda _: cbs.CBSScraper(),
}


class ScrapeManager:
    provider_service = ProviderService(SessionLocal())

    def pick_provider(self):
        provider = self.provider_service.search(
            sort=Sort(field="pos", direction="desc")
        )
        if not provider:
            raise Exception("no provider returned from query")
        return provider

    def run(self, game: Game):
        # provider = self.pick_provider()
        provider = Provider(name="cbs", pos=0)
        scraper = providers[provider.name]()
        html = scraper.get_html(scraper.get_url(game))
        res = scraper.scrape(scraper.parse_html(html), game)
        print(res)
        for player_week in res.player_week_data:
            self.db.write_model(player_week)
        # provider.pos = provider.pos + len(providers)
        # self.db.write_model(provider)


if __name__ == "__main__":
    m = ScrapeManager()
    game = m.db.fetch_model("select * from game where week = -1;", Game)
    if not game:
        print("ah!")
    else:
        m.run(game)
