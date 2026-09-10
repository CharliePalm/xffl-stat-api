from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from shared.model import Game
from scrapers.manager import ScrapeManager
from shared.service.game_service import GameService
from shared.service.engine import SessionLocal
from shared.service.service import Criterion

service = GameService(SessionLocal())


def get_games() -> list[Game]:
    now = datetime.now(ZoneInfo("America/Chicago"))
    lower = now - timedelta(hours=4)
    upper = now + timedelta(minutes=1)

    return service.search(
        Criterion.gte("date_time", lower.strftime("%Y-%m-%d %H:%M:%S"))
        & Criterion.lte("date_time", upper.strftime("%Y-%m-%d %H:%M:%S"))
    ).items


def process_game(game: Game) -> None:
    print(f"Processing {game.away.value} @ {game.home.value}, week {game.week}")
    manager = ScrapeManager()
    manager.run(game)


def run_job():
    games = get_games()
    print(games)
    [process_game(g) for g in games]


if __name__ == "__main__":
    run_job()
