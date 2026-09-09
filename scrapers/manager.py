from typing import Any, Callable

from scrapers.scraper import Scraper
from shared.model import Game
from scrapers import espn, cbs, pff, sleeper
from shared.service.engine import SessionLocal
from shared.service.game_service import GameService
from shared.service.player_week_service import PlayerWeekService
from shared.service.provider import ProviderService
from shared.service.service import Criterion, Page, Sort

providers: dict[str, Callable[[], Scraper[Any]]] = {
    "cbs": lambda: cbs.CBSScraper(),
    "espn": lambda: espn.ESPNScraper(),
    "pff": lambda: pff.PFFScraper(),
    "sleeper": lambda: sleeper.SleeperScraper(),
    # 'nbc': lambda _: cbs.CBSScraper(),
}
game_service = GameService(SessionLocal())


class ScrapeManager:
    provider_service = ProviderService(SessionLocal())
    stat_service = PlayerWeekService(SessionLocal())

    def pick_provider(self):
        provider = self.provider_service.search(
            sort=Sort(field="pos", direction="desc"), page=Page(limit=1)
        ).items[0]
        if not provider:
            raise Exception("no provider returned from query")
        return provider

    def run(self, game: Game):
        provider = self.pick_provider()
        # provider = Provider(name="cbs", pos=0)
        scraper = providers[provider.name]()
        html = scraper.get_html(scraper.get_url(game))
        res = scraper.scrape(scraper.parse_html(html), game)
        print(res)
        for player_week in res.player_week_data:
            self.stat_service.put(id=None, data=player_week)
        provider.pos = provider.pos + len(providers)
        self.provider_service.put(id=None, data=provider)


# "select * from game where week = -1;", Game
if __name__ == "__main__":
    m = ScrapeManager()
    game = game_service.search(Criterion.eq("week", -1)).items[0]
    if not game:
        print("ah!")
    else:
        m.run(game)
