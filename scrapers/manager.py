from typing import Any, Callable

from scrapers.scraper import Scraper
from shared.model import Game, Provider
from scrapers import espn, cbs, pff, sleeper, tank01
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
    "tank01": lambda: tank01.TankScraper(),
    # 'nbc': lambda _: cbs.CBSScraper(),
}


class ScrapeManager:
    def _pick_provider(self, provider_service: ProviderService) -> Provider:
        provider = provider_service.search(
            sort=Sort(field="pos", direction="desc"), page=Page(limit=1)
        ).items[0]
        if not provider:
            raise Exception("no provider returned from query")
        return provider

    def run(self, game: Game) -> None:
        # One session, one transaction, for this run only: opening it as a
        # `with` block is what makes it actually commit (and close) when
        # `run` returns, rather than holding an open write transaction —
        # and the SQLite lock that comes with it — for the rest of the
        # process's life, which is what a class- or module-level session
        # singleton here used to do.
        with SessionLocal() as session, session.begin():
            provider_service = ProviderService(session)
            stat_service = PlayerWeekService(session)

            provider = self._pick_provider(provider_service)
            scraper = providers[provider.name]()
            html = scraper.get_html(scraper.get_url(game))
            res = scraper.scrape(scraper.parse_html(html), game)
            print(res)
            for player_week in res.player_week_data:
                stat_service.put(id=None, data=player_week)
            provider.pos = provider.pos + len(providers)
            provider.uses += 1
            provider_service.put(id=None, data=provider)


if __name__ == "__main__":
    m = ScrapeManager()
    with SessionLocal() as session, session.begin():
        game = GameService(session).search(Criterion.eq("week", -1)).items[0]
    if not game:
        print("ah!")
    else:
        m.run(game)
