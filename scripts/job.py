from shared.model import Game
from shared.db import Database
from scrapers.manager import ScrapeManager

db = Database()


def get_games() -> list[Game]:
    """
    SELECT * FROM game
    WHERE
        gameDateTime BETWEEN datetime('now', 'localtime', '-2 minutes') AND datetime('now', 'localtime', '+5 minutes')
        AND in_progress = 0
    """
    return db.fetch_models("select * from game limit 2", Game)


def process_game(game: Game) -> None:
    """
    Placeholder — fill in with the actual per-game work later
    (e.g. hit an API, mark in_progress, notify somewhere).
    """
    print(f"Processing {game.away.value} @ {game.home.value}, week {game.week}")
    manager = ScrapeManager()
    manager.run(game)


if __name__ == "__main__":
    games = get_games()
    [process_game(g) for g in games]
