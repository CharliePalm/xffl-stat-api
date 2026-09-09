from shared.model import Game
from scrapers.manager import ScrapeManager
from shared.service.game_service import GameService
from shared.service.engine import SessionLocal
from shared.service.service import Criterion, Op

service = GameService(SessionLocal())


def get_games() -> list[Game]:
    return service.search(
        Criterion.between(
            "date_time",
            lower="datetime('now', 'localtime', '-2 minutes')",
            upper="datetime('now', 'localtime', '+5 minutes')",
        )
        & Criterion.eq("in_progress", 0)
    ).items


def process_game(game: Game) -> None:
    print(f"Processing {game.away.value} @ {game.home.value}, week {game.week}")
    manager = ScrapeManager()
    manager.run(game)


def run_job():
    games = get_games()
    [process_game(g) for g in games]
